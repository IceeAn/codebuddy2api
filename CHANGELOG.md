# 更新日志

## 未发布

- 新增 Anthropic Messages wire protocol 兼容 API、合成模型发现、Claude Code 配置支持及管理台 Anthropic playground。
- 支持文本、流式响应、thinking 完整性签名、自定义工具调用与工具结果；接受并忽略 `anthropic-beta`、`output_config` 和未知字段，同时继续拒绝无法转换的媒体、服务端工具和原生 thinking 签名。
- Anthropic 命名空间的框架级 404/405 使用协议错误信封；OpenAPI 声明版本/beta 请求头，管理台切换协议时可取消模型查询。
- 兼容 Claude Code 放在 `messages` 中的 system 角色，并让缺少 usage 的 CodeBuddy `content_filter` 响应以 refusal 正常结束。
- 抽取协议中立的 CodeBuddy SSE 事件和共享聊天执行层，同时保持既有 OpenAI Chat Completions 行为。
- Anthropic 外部路由支持 `x-api-key` 或 Bearer API Key，使用稳定 request ID、协议错误信封和安全响应头。
- 开发依赖加入固定版本 `anthropic==0.116.0`，用于官方 SDK 黑盒契约测试。

## [v0.1.0] - 2026-07-10

- 首个发布版本。支持将 CodeBuddy 订阅转换为 OpenAI Chat Completions 协议接口，在 OpenCode 等第三方工具使用。
