"""
CodeBuddy API Router - 兼容CodeBuddy官方API格式
重构版本 - 优化了代码结构、错误处理和资源管理
"""
import json
import time
import uuid
import logging
import asyncio
from typing import Optional, Dict, Any, List, AsyncGenerator

import httpx
from fastapi import APIRouter, HTTPException, Depends, Request, Header
from fastapi.responses import StreamingResponse

from .auth import AuthenticatedUser, authenticate
from .codebuddy_api_client import codebuddy_api_client
from .codebuddy_token_manager import get_token_manager_for_user
from .usage_stats_manager import usage_stats_manager
from .keyword_replacer import apply_keyword_replacement_to_system_message
logger = logging.getLogger(__name__)

router = APIRouter()

def get_codebuddy_api_url() -> str:
    """动态加载 CodeBuddy API URL，确保安全白名单即时生效。"""
    from config import get_codebuddy_api_endpoint
    return f"{get_codebuddy_api_endpoint()}/v2/chat/completions"

def get_available_models_list() -> List[str]:
    """动态加载可用模型列表，支持设置热更新。"""
    from config import get_available_models
    return get_available_models()

# --- 配置管理 ---
class SecurityConfig:
    """安全配置管理器"""
    
    @staticmethod
    def get_ssl_verify() -> bool:
        """获取SSL验证设置，生产默认启用。"""
        from config import get_ssl_verify
        ssl_verify = get_ssl_verify()
        if not ssl_verify:
            logger.warning("SSL验证已禁用，仅应在受控调试环境使用。")
        return ssl_verify

# --- HTTP 客户端配置 ---
HTTP_CLIENT_CONFIG = {
    "verify": SecurityConfig.get_ssl_verify(),
    "timeout": httpx.Timeout(300.0, connect=30.0, read=300.0),
    "limits": httpx.Limits(max_keepalive_connections=20, max_connections=100)
}

# --- 异步安全的 HTTP 客户端池 ---
_http_client_pool: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()

async def get_http_client() -> httpx.AsyncClient:
    """获取全局 HTTP 客户端池 - 异步安全"""
    global _http_client_pool
    if _http_client_pool is None:
        async with _client_lock:
            # 双重检查锁定模式 - 异步版本
            if _http_client_pool is None:
                _http_client_pool = httpx.AsyncClient(**HTTP_CLIENT_CONFIG)
    return _http_client_pool

async def close_http_client():
    """关闭全局 HTTP 客户端池 - 异步安全"""
    global _http_client_pool
    async with _client_lock:
        if _http_client_pool is not None:
            await _http_client_pool.aclose()
            _http_client_pool = None

# --- 应用生命周期管理 ---
class AppLifecycleManager:
    """应用生命周期管理器 - 处理资源清理"""
    
    @staticmethod
    async def startup():
        """应用启动时的初始化"""
        logger.info("CodeBuddy Router 启动中...")
        # 预热连接池
        await get_http_client()
        logger.info("HTTP 连接池已初始化")
    
    @staticmethod
    async def shutdown():
        """应用关闭时的清理"""
        logger.info("CodeBuddy Router 关闭中...")
        await close_http_client()
        logger.info("资源清理完成")

# 导出生命周期管理器供主应用使用
lifecycle_manager = AppLifecycleManager()

# --- 标准响应头 ---
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive"
}

# --- 辅助函数 ---

def format_sse_error(message: str, error_type: str = "stream_error") -> str:
    """格式化SSE错误响应"""
    error_data = {
        "error": {
            "message": message,
            "type": error_type
        }
    }
    return format_sse_event(error_data)


def format_sse_event(data: Dict[str, Any]) -> str:
    """格式化 data-only SSE 事件，OpenAI 兼容客户端依赖空行作为事件边界。"""
    return f'data: {json.dumps(data, ensure_ascii=False)}\n\n'


def format_sse_done() -> str:
    return "data: [DONE]\n\n"


def ensure_openai_stream_chunk_fields(
    chunk_data: Dict[str, Any],
    stream_id: str,
    created: int,
    model: str,
) -> Dict[str, Any]:
    if not isinstance(chunk_data, dict) or "choices" not in chunk_data:
        return chunk_data

    converted_chunk = chunk_data.copy()
    converted_chunk.setdefault("id", stream_id)
    converted_chunk.setdefault("object", "chat.completion.chunk")
    converted_chunk.setdefault("created", created)
    if model:
        converted_chunk.setdefault("model", model)
    return converted_chunk

class OpenAICompatibilityConverter:
    """将CodeBuddy格式转换为OpenAI兼容格式"""
    
    @staticmethod
    def convert_tool_call_id(codebuddy_id: str) -> str:
        """转换工具调用ID格式: tooluse_xxx -> call_xxx"""
        if codebuddy_id.startswith('tooluse_'):
            return f"call_{codebuddy_id[8:]}"
        return codebuddy_id
    
    @staticmethod
    def convert_sse_chunk_to_openai_format(chunk_data: Dict[str, Any], tool_call_index_map: Dict[str, int]) -> Dict[str, Any]:
        """将CodeBuddy SSE块转换为OpenAI格式"""
        if not chunk_data.get('choices'):
            return chunk_data
        
        choice = chunk_data['choices'][0]
        delta = choice.get('delta', {})
        tool_calls = delta.get('tool_calls', [])
        
        if not tool_calls:
            return chunk_data
        
        # 转换工具调用格式
        converted_tool_calls = []
        for tc in tool_calls:
            converted_tc = tc.copy()
            
            # 转换ID格式
            if tc.get('id'):
                original_id = tc['id']
                converted_id = OpenAICompatibilityConverter.convert_tool_call_id(original_id)
                converted_tc['id'] = converted_id
                
                # 分配新的index
                if original_id not in tool_call_index_map:
                    tool_call_index_map[original_id] = len(tool_call_index_map)
                
                converted_tc['index'] = tool_call_index_map[original_id]
            
            # 如果没有ID，使用当前最新的index
            elif tool_call_index_map:
                # 使用最后一个工具调用的index
                converted_tc['index'] = max(tool_call_index_map.values())
            
            converted_tool_calls.append(converted_tc)
        
        # 更新chunk数据
        converted_chunk = chunk_data.copy()
        converted_chunk['choices'][0]['delta']['tool_calls'] = converted_tool_calls
        
        return converted_chunk

def parse_sse_line(line: str) -> Optional[Dict[str, Any]]:
    """解析单行SSE数据"""
    if not line.startswith('data: '):
        return None
    
    data = line[6:].strip()
    if not data or data == '[DONE]':
        return None
    
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None

def validate_and_fix_tool_call_args(args: str) -> str:
    """增强版的工具调用参数验证和修复 - 专门处理多工具调用问题"""
    if not args:
        return '{}'
    
    args = args.strip()
    
    # 检查是否是多个JSON对象连接的情况 - 这是多工具调用的主要问题
    if args.count('}{') > 0:
        # 尝试分离多个JSON对象
        json_objects = []
        current_obj = ""
        brace_count = 0
        
        for i, char in enumerate(args):
            current_obj += char
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0 and current_obj.strip():
                    # 完成了一个JSON对象
                    try:
                        parsed = json.loads(current_obj.strip())
                        json_objects.append(parsed)
                        current_obj = ""
                    except json.JSONDecodeError:
                        current_obj = ""
        
        if json_objects:
            return json.dumps(json_objects[0], ensure_ascii=False)
    
    # 原有的修复逻辑
    try:
        json.loads(args)
        return args
    except json.JSONDecodeError as e:
        
        
        # 尝试修复常见的JSON问题
        original_args = args
        if not args.endswith('}') and args.count('{') > args.count('}'):
            args += '}'
            
        elif not args.endswith(']') and args.count('[') > args.count(']'):
            args += ']'
            
        
        try:
            json.loads(args)
            
            return args
        except json.JSONDecodeError:
            return '{}'

class SSEConnectionManager:
    """SSE 连接管理器，包含重连逻辑"""
    
    def __init__(self, max_retries: int = 3, retry_delay: float = 1.0):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
    
    async def stream_with_retry(self, stream_func, *args, **kwargs):
        """带重连的流式处理"""
        for attempt in range(self.max_retries + 1):
            try:
                async for chunk in stream_func(*args, **kwargs):
                    yield chunk
                break  # 成功完成，退出重试循环
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt < self.max_retries:
                    wait_time = self.retry_delay * (2 ** attempt)  # 指数退避: 1s, 2s, 4s
                    logger.warning(f"连接失败，{wait_time}秒后重试 (第{attempt + 1}次): {e}")
                    yield format_sse_error(f"Connection lost, retrying in {wait_time}s... (attempt {attempt + 1})", "connection_retry")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(f"重连失败，已达到最大重试次数: {e}")
                    yield format_sse_error(f"Connection failed after {self.max_retries} retries: {str(e)}", "connection_failed")
                    raise
            except Exception as e:
                # 其他异常不重试，直接抛出
                logger.error(f"流式处理异常: {e}")
                yield format_sse_error(f"Stream error: {str(e)}", "stream_error")
                raise

class StreamResponseAggregator:
    """流式响应聚合器 - 修复多工具调用问题：使用工具调用ID作为键"""
    
    def __init__(self):
        self.data = {
            "id": None,
            "model": None,
            "content": "",
            "tool_calls": [],
            "finish_reason": None,
            "usage": None,
            "system_fingerprint": None
        }
        # 🔑 关键：使用工具调用ID作为键，因为index都是0会覆盖
        self.tool_call_map = {}  # key: tool_call_id, value: tool_call_data
        self.tool_call_order = []  # 保持工具调用的接收顺序
        self.current_tool_id = None  # 当前正在处理的工具调用ID
    
    def process_chunk(self, obj: Dict[str, Any]):
        """处理单个响应块"""
        # 聚合基本信息
        self.data["id"] = self.data["id"] or obj.get('id')
        self.data["model"] = self.data["model"] or obj.get('model')
        self.data["system_fingerprint"] = obj.get('system_fingerprint') or self.data["system_fingerprint"]
        
        if obj.get('usage'):
            self.data["usage"] = obj.get('usage')
        
        choices = obj.get('choices', [])
        if not choices:
            return
        
        choice = choices[0]
        if choice.get('finish_reason'):
            self.data["finish_reason"] = choice.get('finish_reason')
        
        delta = choice.get('delta', {})
        
        # 聚合内容
        if delta.get('content'):
            self.data["content"] += delta.get('content')
        
        # 处理工具调用
        if delta.get('tool_calls'):
            self._process_tool_calls(delta.get('tool_calls'))
    
    def _process_tool_calls(self, tool_calls: List[Dict[str, Any]]):
        """处理工具调用 - 修复版：使用工具调用ID，正确处理分块传输"""
        for tc in tool_calls:
            tool_id = tc.get('id')
            
            # 如果有ID，这是一个新的工具调用
            if tool_id:
                # 新工具调用
                if tool_id not in self.tool_call_map:
                    self.tool_call_map[tool_id] = {
                        'id': tool_id,
                        'type': tc.get('type', 'function'),
                        'function': {
                            'name': '',
                            'arguments': ''
                        }
                    }
                    self.tool_call_order.append(tool_id)
                    self.current_tool_id = tool_id
                    logger.info(f"🔧 新工具调用: {tool_id}")
                else:
                    # 更新当前工具调用ID
                    self.current_tool_id = tool_id
                
                # 更新工具调用信息
                if tc.get('type'):
                    self.tool_call_map[tool_id]['type'] = tc.get('type')
                
                func = tc.get('function', {})
                if func.get('name'):
                    self.tool_call_map[tool_id]['function']['name'] = func.get('name')
                if func.get('arguments'):
                    self.tool_call_map[tool_id]['function']['arguments'] += func.get('arguments')
            
            # 如果没有ID，但有当前工具调用ID，这是增量数据
            elif self.current_tool_id and self.current_tool_id in self.tool_call_map:
                func = tc.get('function', {})
                if func.get('name'):
                    self.tool_call_map[self.current_tool_id]['function']['name'] = func.get('name')
                if func.get('arguments'):
                    self.tool_call_map[self.current_tool_id]['function']['arguments'] += func.get('arguments')
            
            else:
                # 没有ID且没有当前工具调用，跳过
                logger.warning("⚠️ 工具调用缺少ID且无当前工具调用上下文，跳过处理")
    
    def finalize(self) -> Dict[str, Any]:
        """完成聚合并返回最终响应"""
        # 按接收顺序构建工具调用列表
        if self.tool_call_map:
            self.data["tool_calls"] = []
            for tool_id in self.tool_call_order:
                if tool_id in self.tool_call_map:
                    tc = self.tool_call_map[tool_id]
                    # 验证和修复工具调用参数
                    tc['function']['arguments'] = validate_and_fix_tool_call_args(
                        tc['function']['arguments']
                    )
                    self.data["tool_calls"].append(tc)
                    logger.info(f"📋 工具调用: {tool_id} - {tc['function']['name']}")
            
            logger.info(f"✅ 成功聚合 {len(self.data['tool_calls'])} 个工具调用")
        
        # 构建最终响应
        final_message = {"role": "assistant", "content": self.data["content"]}
        if self.data["tool_calls"]:
            final_message["tool_calls"] = self.data["tool_calls"]
        
        finish_reason = "tool_calls" if self.data["tool_calls"] else (self.data["finish_reason"] or "stop")
        
        final_response = {
            "id": self.data["id"] or str(uuid.uuid4()),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.data["model"] or "unknown",
            "choices": [
                {
                    "index": 0,
                    "message": final_message,
                    "finish_reason": finish_reason,
                    "logprobs": None
                }
            ]
        }
        
        if self.data["usage"]:
            final_response["usage"] = self.data["usage"]
        if self.data["system_fingerprint"]:
            final_response["system_fingerprint"] = self.data["system_fingerprint"]
        
        return final_response

class CodeBuddyStreamService:
    """CodeBuddy 流式服务类 - 职责分离，使用连接池优化"""
    
    def __init__(self):
        self.connection_manager = SSEConnectionManager(max_retries=3, retry_delay=1.0)
    
    def _handle_api_error(self, status_code: int, error_msg: str) -> None:
        """统一的API错误处理 - 直接抛出异常"""
        logger.error(f"CodeBuddy API错误: {status_code} - {error_msg}")
        
        if status_code == 401:
            raise HTTPException(status_code=401, detail="CodeBuddy API authentication failed")
        elif status_code == 429:
            raise HTTPException(status_code=429, detail="CodeBuddy API rate limit exceeded")
        elif status_code >= 500:
            raise HTTPException(status_code=502, detail="CodeBuddy API server error")
        else:
            raise HTTPException(status_code=status_code, detail=f"CodeBuddy API error: {error_msg}")
    
    async def handle_stream_response(self, payload: Dict[str, Any], headers: Dict[str, str]) -> StreamingResponse:
        """处理流式响应 - 使用OpenAI兼容性转换器修复格式问题"""
        async def stream_core():
            client = await get_http_client()
            async with client.stream("POST", get_codebuddy_api_url(), json=payload, headers=headers) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    error_msg = error_text.decode('utf-8', errors='ignore')
                    yield format_sse_error(f"CodeBuddy API error: {response.status_code} - {error_msg}", "api_error")
                    return
                
                buffer = ""
                tool_call_index_map = {}  # 用于跟踪工具调用ID到index的映射
                fallback_stream_id = f"chatcmpl-{uuid.uuid4().hex}"
                fallback_created = int(time.time())
                fallback_model = str(payload.get("model") or "unknown")

                async for chunk in response.aiter_text(chunk_size=8192):
                    if not chunk:
                        continue
                    
                    buffer += chunk
                    
                    # 处理完整的SSE行
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        
                        # 跳过空行和注释行
                        if not line.strip() or line.startswith(':'):
                            continue
                        
                        # 检查是否结束
                        if '[DONE]' in line:
                            yield format_sse_done()
                            return
                        
                        # 解析SSE数据
                        chunk_data = parse_sse_line(line)
                        if chunk_data:
                            # 🔑 关键修改：使用OpenAI兼容性转换器
                            converted_chunk = OpenAICompatibilityConverter.convert_sse_chunk_to_openai_format(
                                chunk_data, tool_call_index_map
                            )
                            converted_chunk = ensure_openai_stream_chunk_fields(
                                converted_chunk,
                                fallback_stream_id,
                                fallback_created,
                                fallback_model,
                            )
                            
                            # 重新格式化为SSE格式并发送
                            yield format_sse_event(converted_chunk)
                
                # 处理缓冲区中剩余的数据
                if buffer.strip():
                    remaining = buffer.strip()
                    if "[DONE]" in remaining:
                        yield format_sse_done()
                    else:
                        chunk_data = parse_sse_line(remaining)
                        if not chunk_data:
                            return
                        converted_chunk = OpenAICompatibilityConverter.convert_sse_chunk_to_openai_format(
                            chunk_data, tool_call_index_map
                        )
                        converted_chunk = ensure_openai_stream_chunk_fields(
                            converted_chunk,
                            fallback_stream_id,
                            fallback_created,
                            fallback_model,
                        )
                        yield format_sse_event(converted_chunk)
        
        async def stream_with_retry():
            async for chunk in self.connection_manager.stream_with_retry(stream_core):
                yield chunk
        
        return StreamingResponse(stream_with_retry(), media_type="text/event-stream", headers=SSE_HEADERS)
    
    async def handle_non_stream_response(self, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        """处理非流式响应 - 使用修复后的聚合器，支持多工具调用"""
        try:
            client = await get_http_client()
            response = await client.post(get_codebuddy_api_url(), json=payload, headers=headers)
            
            if response.status_code != 200:
                error_msg = response.text
                self._handle_api_error(response.status_code, error_msg)
            
            aggregator = StreamResponseAggregator()
            buffer = ""
            
            async for chunk in response.aiter_text():
                if not chunk:
                    continue
                buffer += chunk
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    obj = parse_sse_line(line)
                    if obj:
                        aggregator.process_chunk(obj)
            
            if buffer.strip():
                obj = parse_sse_line(buffer.strip())
                if obj:
                    aggregator.process_chunk(obj)
            
            return aggregator.finalize()
            
        except httpx.TimeoutException:
            logger.error("CodeBuddy API 超时")
            raise HTTPException(status_code=504, detail="CodeBuddy API timeout")
        except httpx.NetworkError as e:
            logger.error(f"网络错误: {e}")
            raise HTTPException(status_code=502, detail=f"Network error: {str(e)}")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"请求异常: {e}")
            raise HTTPException(status_code=500, detail=f"Request error: {str(e)}")

class RequestProcessor:
    """请求预处理器 - 线程安全的请求处理"""
    
    @staticmethod
    def prepare_payload(request_body: Dict[str, Any]) -> Dict[str, Any]:
        """准备请求载荷"""
        payload = request_body.copy()
        if not payload.get("model"):
            from config import DEFAULT_CODEBUDDY_MODELS, get_available_models
            payload["model"] = next(
                (model for model in get_available_models() if model),
                DEFAULT_CODEBUDDY_MODELS[0],
            )
        payload["stream"] = True  # CodeBuddy 只支持流式请求
        
        # 处理消息长度要求：CodeBuddy要求至少2条消息
        messages = payload.get("messages", [])
        if len(messages) == 1 and messages[0].get("role") == "user":
            system_msg = {"role": "system", "content": "You are a helpful assistant."}
            payload["messages"] = [system_msg] + messages
        
        # 应用关键词替换
        for msg in payload.get("messages", []):
            if msg.get("role") == "system":
                msg["content"] = apply_keyword_replacement_to_system_message(msg.get("content"))
        
        return payload
    
    @staticmethod
    def validate_request(request_body: Dict[str, Any]) -> None:
        """验证请求参数"""
        if not isinstance(request_body, dict):
            raise HTTPException(status_code=400, detail="Request body must be a JSON object")
        
        messages = request_body.get("messages")
        if not messages or not isinstance(messages, list):
            raise HTTPException(status_code=400, detail="Messages field is required and must be an array")
        
        if not messages:
            raise HTTPException(status_code=400, detail="At least one message is required")
        
        # 验证消息格式
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise HTTPException(status_code=400, detail=f"Message {i} must be an object")
            if "role" not in msg or "content" not in msg:
                raise HTTPException(status_code=400, detail=f"Message {i} must have 'role' and 'content' fields")

class CredentialManager:
    """凭证管理器 - 线程安全的凭证获取"""
    
    @staticmethod
    def get_valid_credential(token_manager) -> Dict[str, Any]:
        """获取有效凭证，包含错误处理"""
        try:
            credential = token_manager.get_next_credential()
            if not credential:
                raise HTTPException(status_code=401, detail="没有可用的CodeBuddy凭证")
            
            bearer_token = credential.get('bearer_token')
            if not bearer_token:
                raise HTTPException(status_code=401, detail="无效的CodeBuddy凭证")
            
            return credential
        except Exception as e:
            logger.error(f"获取凭证失败: {e}")
            raise HTTPException(status_code=401, detail="凭证获取失败")

# --- API Endpoints ---

@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    x_conversation_id: Optional[str] = Header(None, alias="X-Conversation-ID"),
    x_conversation_request_id: Optional[str] = Header(None, alias="X-Conversation-Request-ID"),
    x_conversation_message_id: Optional[str] = Header(None, alias="X-Conversation-Message-ID"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID"),
    _user: AuthenticatedUser = Depends(authenticate)
):
    """CodeBuddy V1 聊天完成API - 重构后的简洁版本"""
    try:
        # 解析和验证请求体
        try:
            request_body = await request.json()
        except Exception as e:
            logger.error(f"解析请求体失败: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid JSON request body: {str(e)}")
        
        # 验证请求参数
        RequestProcessor.validate_request(request_body)
        
        # 获取有效凭证
        token_manager = get_token_manager_for_user(_user)
        credential = CredentialManager.get_valid_credential(token_manager)
        
        # 生成请求头
        headers = codebuddy_api_client.generate_codebuddy_headers(
            bearer_token=credential.get('bearer_token'),
            user_id=credential.get('user_id'),
            domain=credential.get('domain'),
            conversation_id=x_conversation_id,
            conversation_request_id=x_conversation_request_id,
            conversation_message_id=x_conversation_message_id,
            request_id=x_request_id
        )
        
        # 预处理请求
        payload = RequestProcessor.prepare_payload(request_body)
        usage_stats_manager.record_model_usage(payload.get("model", "unknown"))
        
        # 使用服务类处理请求
        service = CodeBuddyStreamService()
        client_wants_stream = request_body.get("stream", False)
        
        if client_wants_stream:
            return await service.handle_stream_response(payload, headers)
        else:
            return await service.handle_non_stream_response(payload, headers)
                
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CodeBuddy V1 API错误: {e}")
        raise HTTPException(status_code=500, detail=f"内部服务器错误: {str(e)}")

@router.get("/v1/models")
async def list_v1_models(_user: AuthenticatedUser = Depends(authenticate)):
    """获取CodeBuddy V1模型列表"""
    try:
        return {
            "object": "list",
            "data": [{
                "id": model,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "codebuddy"
            } for model in get_available_models_list()]
        }
        
    except Exception as e:
        logger.error(f"获取V1模型列表错误: {e}")
        raise HTTPException(status_code=500, detail="获取模型列表失败")

@router.get("/v1/credentials", summary="List all available credentials")
async def list_credentials(_user: AuthenticatedUser = Depends(authenticate)):
    """列出所有可用凭证的详细信息，包括过期状态"""
    try:
        token_manager = get_token_manager_for_user(_user)
        credentials_info = token_manager.get_credentials_info()
        safe_credentials = []
        
        credentials = token_manager.get_all_credentials()
        
        for info in credentials_info:
            bearer_token = credentials[info['index']].get("bearer_token", "") if info['index'] < len(credentials) else ""
            
            # 格式化时间显示
            if info['time_remaining'] is not None and info['time_remaining'] > 0:
                days, remainder = divmod(info['time_remaining'], 86400)
                hours, remainder = divmod(remainder, 3600)
                minutes = remainder // 60
                time_remaining_str = f"{days}d {hours}h" if days > 0 else f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
            else:
                time_remaining_str = "Expired" if info['time_remaining'] is not None else "Unknown"
            
            safe_credentials.append({
                **info,  # 展开所有原始信息
                "time_remaining_str": time_remaining_str,
                "has_token": bool(bearer_token),
                "token_preview": f"{bearer_token[:10]}...{bearer_token[-4:]}" if len(bearer_token) > 14 else "Invalid Token"
            })
        
        return {"credentials": safe_credentials}
        
    except Exception as e:
        logger.error(f"获取凭证列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/credentials", summary="Add a new credential")
async def add_credential(
    request: Request,
    _user: AuthenticatedUser = Depends(authenticate)
):
    """添加一个新的认证凭证"""
    try:
        data = await request.json()
        if not data.get("bearer_token"):
            raise HTTPException(status_code=422, detail="bearer_token is required")

        token_manager = get_token_manager_for_user(_user)
        success = token_manager.add_credential(
            data.get("bearer_token"),
            data.get("user_id")
        )
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save credential file")
        
        return {"message": "Credential added successfully"}

    except Exception as e:
        logger.error(f"添加凭证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/credentials/select", summary="Manually select a credential")
async def select_credential(
    request: Request,
    _user: AuthenticatedUser = Depends(authenticate)
):
    """手动选择指定的凭证"""
    try:
        data = await request.json()
        index = data.get("index")
        if index is None:
            raise HTTPException(status_code=422, detail="index is required")

        token_manager = get_token_manager_for_user(_user)
        if not token_manager.set_manual_credential(index):
            raise HTTPException(status_code=400, detail="Invalid credential index")
        
        return {"message": f"Credential #{index + 1} selected successfully"}

    except Exception as e:
        logger.error(f"选择凭证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/credentials/auto", summary="Resume automatic credential rotation")
async def resume_auto_rotation(_user: AuthenticatedUser = Depends(authenticate)):
    """恢复自动凭证轮换"""
    try:
        token_manager = get_token_manager_for_user(_user)
        token_manager.clear_manual_selection()
        return {"message": "Resumed automatic credential rotation"}

    except Exception as e:
        logger.error(f"恢复自动轮换失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/credentials/toggle-rotation", summary="Toggle automatic credential rotation")
async def toggle_auto_rotation(_user: AuthenticatedUser = Depends(authenticate)):
    """切换自动轮换开关"""
    try:
        token_manager = get_token_manager_for_user(_user)
        is_enabled = token_manager.toggle_auto_rotation()
        status = "enabled" if is_enabled else "disabled"
        message = f"Auto rotation {status}"
        return {
            "message": message,
            "auto_rotation_enabled": is_enabled
        }

    except Exception as e:
        logger.error(f"切换自动轮换失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/v1/credentials/current", summary="Get current credential info")
async def get_current_credential(_user: AuthenticatedUser = Depends(authenticate)):
    """获取当前使用的凭证信息"""
    try:
        token_manager = get_token_manager_for_user(_user)
        info = token_manager.get_current_credential_info()
        return info

    except Exception as e:
        logger.error(f"获取当前凭证信息失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/credentials/delete", summary="Delete a credential by index")
async def delete_credential(request: Request, _user: AuthenticatedUser = Depends(authenticate)):
    """删除一个凭证文件（通过索引）并从列表中移除"""
    try:
        data = await request.json()
        index = data.get("index")
        if index is None or not isinstance(index, int):
            raise HTTPException(status_code=422, detail="Valid integer index is required")

        token_manager = get_token_manager_for_user(_user)
        if not token_manager.delete_credential_by_index(index):
            raise HTTPException(status_code=400, detail="Invalid index or failed to delete credential")

        return {"message": f"Credential #{index + 1} deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除凭证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
