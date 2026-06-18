# AGENTS.md

## 常用命令

```bash
# 开发服务器（需要 venv）
source venv/bin/activate && python3 web.py

# Docker 部署
docker compose up -d --build

# 运行测试（unittest，不是 pytest）
python -m unittest tests/test_security.py

# 为 secrets/users.txt 生成密码哈希
python3 scripts/hash_password.py <用户名>
```

## 架构

- **入口点**: `web.py` — FastAPI 应用，使用 **Hypercorn**（不是 uvicorn）提供服务，通过 `asyncio.run(serve(app, config))` 启动。
- **配置**: `config.py` — 热更新配置优先级为：运行时内存 > `config/config.json` > 环境变量 > 硬编码默认值。仅 `_HOT_RELOADABLE_CONFIG_KEYS` 中的配置项（日志级别、模型列表、轮换次数）会持久化到 JSON；其他安全边界配置不会从 JSON 加载，实际优先级为环境变量 > 硬编码默认值。
- **认证**: `src/auth.py` 中包含两个独立认证层：
  - Web UI 登录 → HttpOnly 会话 Cookie（7 天有效期），通过 `secrets/users.txt`（PBKDF2 哈希，每行格式 `用户名:哈希`）验证。
  - API 访问 → 通过 Web UI 生成的 `sk-...` Bearer Token，以密码哈希形式存储在 `.codebuddy_creds/api_keys.json`（0600 权限）。
- **凭证隔离**: 每个系统用户在 `.codebuddy_creds/users/<哈希目录名>/` 下拥有独立的 CodeBuddy Token 目录。Token 管理器按用户单例化（`CodeBuddyTokenManagerRegistry`）。
- **上游 API**: CodeBuddy 仅支持流式响应。客户端的非流式请求通过 SSE 聚合处理（`StreamResponseAggregator`）。所有请求体在 `RequestProcessor.prepare_payload()` 中强制注入 `stream=True`。

## 特殊约定与注意事项

- **强制推理**: `deepseek-v4-pro`、`deepseek-v4-flash` 和 `glm-5.1` 始终被强制设置 `reasoning_effort="max"` 及指定的 `thinking` 选项，忽略客户端请求中的设置。
- **关键词替换**: 系统消息中自动将 Anthropic/Claude 相关引用替换为 Tencent/CodeBuddy（`src/keyword_replacer.py`）。
- **工具调用 ID 兼容**: 透传上游工具调用 ID，仅为流式响应补齐 OpenAI 客户端需要的 `index`（`OpenAICompatibilityConverter`）。
- **流式标准化**: `OpenAIStreamNormalizer` 将混合的 `reasoning_content`+`content` delta 拆分为独立块，并在首个块中注入 `role: assistant`。
- **端点前缀**: 聊天 API 路径为 `POST /codebuddy/v1/chat/completions`，客户端应将 `base_url` 设置为包含 `/codebuddy/v1`。
- **文件安全**: 凭证写入使用 `O_NOFOLLOW` + 0600 权限。加载时跳过 `.codebuddy_creds/` 中的符号链接。文件名经过路径穿越清理。

## Docker

- 容器以非 root 用户 `appuser`（UID 1001）运行。`entrypoint.sh` 使用 `gosu` 切换用户。
- 挂载卷：`./config`、`./.codebuddy_creds`、`./secrets/users.txt:ro`。
- 容器 CMD 为 `hypercorn web:app --bind 0.0.0.0:8001`。

## 安全边界

- `CODEBUDDY_ALLOWED_API_ENDPOINTS` 是硬白名单，非白名单内的 API 端点会回退到默认值。
- `X-Domain` 请求头经过 `[A-Za-z0-9.-]+` 正则过滤，防止请求头注入。
- 对不存在/无效的用户执行虚拟密码哈希验证，防止基于响应时间的用户枚举。
