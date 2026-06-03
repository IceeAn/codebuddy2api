"""
CodeBuddy API Client - 直接调用CodeBuddy API
"""
import json
import time
import uuid
import secrets
import httpx
import logging
import re
from typing import Dict, Any, Optional, AsyncGenerator, List

logger = logging.getLogger(__name__)


def _safe_domain_header(domain: str, fallback: str) -> str:
    value = str(domain or "").strip()
    if not value:
        return fallback
    if re.fullmatch(r"[A-Za-z0-9.-]+", value):
        return value
    logger.warning("Ignoring unsafe CodeBuddy domain header value")
    return fallback


class CodeBuddyAPIClient:
    """CodeBuddy API客户端"""
    
    def __init__(self):
        from config import get_codebuddy_api_endpoint
        self.base_url = get_codebuddy_api_endpoint()
        self.api_endpoint = self.base_url  # 直接使用base_url，不需要plugin前缀
        
    def convert_openai_to_codebuddy_messages(self, openai_messages: List[Dict]) -> List[Dict]:
        """将OpenAI格式消息转换为CodeBuddy格式"""
        codebuddy_messages = []
        
        # 过滤掉包含错误信息的消息，防止触发11128渠道检测
        filtered_messages = []
        for msg in openai_messages:
            content = msg.get("content", "")
            # 跳过包含API错误信息的助手消息
            if (msg.get("role") == "assistant" and 
                isinstance(content, str) and 
                ("Error: API error" in content or "API error:" in content)):
                continue
            filtered_messages.append(msg)
        
        # CodeBuddy要求至少2条消息，如果只有1条用户消息，添加系统消息
        if len(filtered_messages) == 1 and filtered_messages[0].get("role") == "user":
            system_msg = {
                "role": "system",
                "content": "You are a helpful assistant."
            }
            codebuddy_messages.append(system_msg)
        
        for msg in filtered_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            logger.debug(f"[DEBUG] Processing message - role: {role}, content type: {type(content)}")
            
            # 处理特殊的tool角色，转换为user角色
            if role == "tool":
                role = "user"
                logger.info(f"[ROLE_CONVERSION] Converting 'tool' role to 'user'")
            
            # 检查是否包含工具调用相关内容
            has_tool_content = False
            
            # 检查字符串化的JSON内容
            if isinstance(content, str) and content.startswith('[{') and content.endswith('}]'):
                try:
                    parsed_content = json.loads(content)
                    if isinstance(parsed_content, list):
                        content = parsed_content
                        logger.info(f"[JSON_PARSE] Parsed stringified JSON content")
                except json.JSONDecodeError:
                    pass
            
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") in ["tool_result", "tool_use"]:
                        has_tool_content = True
                        break
            
            if has_tool_content:
                # 包含工具调用内容，保持结构化格式
                logger.info(f"[TOOL_CONTENT] Preserving structured content for role: {role}")
                
                # 确保工具结果有正确的toolUseId
                processed_content = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "tool_result":
                            # 确保toolUseId存在且有效
                            tool_use_id = item.get("toolUseId") or item.get("tool_use_id") or item.get("id")
                            if not tool_use_id:
                                # 生成一个有效的toolUseId
                                tool_use_id = f"tool_{uuid.uuid4().hex[:8]}"
                                logger.warning(f"[TOOL_RESULT] Missing toolUseId, generated: {tool_use_id}")
                            
                            # 确保toolUseId符合正则表达式要求 [a-zA-Z0-9_-]+
                            if not tool_use_id or not all(c.isalnum() or c in '_-' for c in tool_use_id):
                                tool_use_id = f"tool_{uuid.uuid4().hex[:8]}"
                                logger.warning(f"[TOOL_RESULT] Invalid toolUseId format, regenerated: {tool_use_id}")
                            
                            # 标准化工具结果格式
                            tool_result = {
                                "type": "tool_result",
                                "toolUseId": tool_use_id,
                                "content": item.get("content", item.get("text", ""))
                            }
                            processed_content.append(tool_result)
                            logger.info(f"[TOOL_RESULT] Processed tool result with toolUseId: {tool_use_id}")
                        elif item.get("type") == "tool_use":
                            # 确保工具使用有正确的id
                            tool_id = item.get("id") or f"tool_{uuid.uuid4().hex[:8]}"
                            tool_use = {
                                "type": "tool_use",
                                "id": tool_id,
                                "name": item.get("name", ""),
                                "input": item.get("input", {})
                            }
                            processed_content.append(tool_use)
                            logger.info(f"[TOOL_USE] Processed tool use with id: {tool_id}")
                        elif item.get("type") == "text":
                            # 处理纯文本内容
                            processed_content.append(item)
                        else:
                            # 其他类型，可能是工具结果的简化格式
                            if "text" in item and not item.get("type"):
                                # 可能是工具结果，转换为标准格式
                                tool_use_id = f"tool_{uuid.uuid4().hex[:8]}"
                                tool_result = {
                                    "type": "tool_result",
                                    "toolUseId": tool_use_id,
                                    "content": item.get("text", "")
                                }
                                processed_content.append(tool_result)
                                logger.info(f"[TOOL_RESULT] Converted text item to tool result with toolUseId: {tool_use_id}")
                            else:
                                processed_content.append(item)
                    else:
                        processed_content.append(item)
                
                codebuddy_msg = {
                    "role": role,
                    "content": processed_content
                }
            else:
                # 普通文本内容，转换为字符串
                if isinstance(content, str):
                    text_content = content
                elif isinstance(content, list):
                    text_parts = []
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("type") == "text":
                                text_parts.append(item.get("text", ""))
                            else:
                                text_parts.append(json.dumps(item, ensure_ascii=False))
                        elif isinstance(item, str):
                            text_parts.append(item)
                        else:
                            text_parts.append(str(item))
                    text_content = "".join(text_parts)
                else:
                    text_content = str(content) if content is not None else ""

                codebuddy_msg = {
                    "role": role,
                    "content": text_content
                }
            
            codebuddy_messages.append(codebuddy_msg)
        
        return codebuddy_messages

    def generate_codebuddy_headers(
        self,
        bearer_token: str,
        user_id: str = None,
        domain: str = None,
        conversation_id: Optional[str] = None,
        conversation_request_id: Optional[str] = None,
        conversation_message_id: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> Dict[str, str]:
        """
        生成CodeBuddy API所需的完整请求头。
        优先使用传入的会话ID，如果未提供则随机生成。
        """
        from config import get_codebuddy_api_host
        codebuddy_host = get_codebuddy_api_host()
        codebuddy_domain = _safe_domain_header(domain, codebuddy_host)
        headers = {
            'Host': codebuddy_host,
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
            'x-stainless-arch': 'x64',
            'x-stainless-lang': 'js',
            'x-stainless-os': 'Linux',
            'x-stainless-package-version': '5.10.1',
            'x-stainless-retry-count': '0',
            'x-stainless-runtime': 'node',
            'x-stainless-runtime-version': 'v22.13.1',
            'X-Conversation-ID': conversation_id or str(uuid.uuid4()),
            'X-Conversation-Request-ID': conversation_request_id or secrets.token_hex(16),
            'X-Conversation-Message-ID': conversation_message_id or str(uuid.uuid4()).replace('-', ''),
            'X-Request-ID': request_id or str(uuid.uuid4()).replace('-', ''),
            'X-Agent-Intent': 'craft',
            'X-IDE-Type': 'CLI',
            'X-IDE-Name': 'CLI',
            'X-IDE-Version': '1.0.7',
            'Authorization': f'Bearer {bearer_token}',
            'X-Domain': codebuddy_domain,
            'User-Agent': 'CLI/1.0.7 CodeBuddy/1.0.7',
            'X-Product': 'SaaS',
            'X-User-Id': user_id or 'b5be3a67-237e-4ee6-9b9a-0b9ecd7b454b'
        }
        return headers


# 全局客户端实例
codebuddy_api_client = CodeBuddyAPIClient()
