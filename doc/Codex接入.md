# Codex CLI 接入

本项目通过 `POST /openai/v1/responses` 将 Codex CLI 请求转换为 CodeBuddy 的流式 Chat Completions。本文以 **Codex CLI 0.153.4** 为验证基线，覆盖命令执行、`apply_patch`、图片输入和 `view_image`、客户端 MCP／工具搜索、历史回放、会话恢复及客户端自动压缩。IDE 扩展与桌面应用尚未单独验收。

## 配置

先在管理台添加 CodeBuddy 凭证并创建 API Key。模型使用 `/openai/v1/models` 返回的真实 ID，不要改成 OpenAI 模型名。

将 [Codex模型目录示例.json](Codex模型目录示例.json) 复制到自己的配置目录；示例包含 `kimi-k3-1`，使用其他模型时复制该条目并修改 `slug`、显示名称及真实支持的输入模态。该文件是 Codex 的本地能力声明，不会增加上游模型能力。它显式启用自由文本 `apply_patch`、图片和客户端工具搜索；仅填写一个 Codex 不认识的模型 ID，可能缺少这些工具。

在 `~/.codex/config.toml` 中加入以下配置，并替换模型目录的绝对路径：

```toml
model_provider = "codebuddy"
model = "kimi-k3-1"
model_catalog_json = "/绝对路径/Codex模型目录示例.json"
web_search = "disabled"

[model_providers.codebuddy]
name = "CodeBuddy2API"
base_url = "http://127.0.0.1:8001/openai/v1"
env_key = "CODEBUDDY_API_KEY"
wire_api = "responses"
supports_websockets = false
```

在启动 Codex 的环境中设置 `CODEBUDDY_API_KEY`，其值为管理台生成的 `sk-...` 密钥，然后运行 `codex`。该密钥不是 CodeBuddy 登录 token。保留自己需要的审批和沙箱设置。

`base_url` 不带 `/responses` 后缀，不使用管理台 playground 路径。`web_search` 必须关闭：OpenAI 托管网页搜索不在本项目实现范围，需要联网检索时可配置客户端 MCP 工具。配置字段参考 [Codex 官方配置文档](https://learn.chatgpt.com/docs/config-file/config-reference)。

## 上下文与会话

Codex 负责保留本地历史，每轮将消息、工具调用和结果随 `input` 一起发送。`codex resume` 恢复的是客户端会话；网关不保存 Responses 对话。不要设置 `store=true` 或使用 `previous_response_id`。

此自定义 provider 下，0.153.4 的自动压缩使用普通 `/responses` 请求生成摘要，然后由客户端替换历史，不依赖 `/responses/compact`。如需调整压缩时机，在 TOML 顶层配置 `model_context_window` 和 `model_auto_compact_token_limit`，数值应根据实际 CodeBuddy 模型的上下文上限设置，后者需为后续输出留出空间。模型目录示例不宣称模型具有 OpenAI 模型的上下文容量。

图片可通过 CLI 的 `--image` 参数传入，也可由 `view_image` 工具返回。网关传递 URL／Base64，不下载图片；视觉能力由所选模型决定。管理台测试页面目前只提供文本输入。

## 自动审批模型映射

请求中的模型名精确等于 `codex-auto-review` 时，网关将它映射为当前用户配置的真实模型。管理台「设置 → 服务配置 → Codex 自动审批模型」可修改并立即生效，重启后仍保留；其他用户不受影响。

优先级为：用户已保存值 → 环境变量 `CODEBUDDY_CODEX_AUTO_REVIEW_MODEL` → 内置默认值 `deepseek-v4-flash`。例如启动环境可设置：

```dotenv
CODEBUDDY_CODEX_AUTO_REVIEW_MODEL=kimi-k3-1
```

显式空值、非法模型名或映射回 `codex-auto-review` 会失败。映射只执行一次，随后对目标应用原有的前缀剥离、强制推理及温度策略；响应中的 model 仍保留请求别名，统计和上游请求使用处理后的真实模型。该映射不会自动开启 Codex 的自动审批，也不改变客户端审批策略。

测试覆盖配置默认值、环境变量、用户隔离、持久化、管理台编辑和 Responses 路由映射。固定版本 CLI 契约测试中的 MCP echo 是显式批准的合成工具；它不验证真实模型作出自动审批决定的质量。

## 工具兼容方式

- `function` 工具直接转换；命名空间工具使用稳定别名交给 CodeBuddy，返回时恢复原名称和命名空间。上游 `call_id` 原样保留。
- `custom` 工具统一包装为只有 `input` 字符串的 JSON function。上游完整返回该 JSON 后，网关恢复原始自由文本，包括换行和引号。`apply_patch` 使用这一流程。
- Lark／regex 格式进入工具说明，网关不执行语法约束、修补参数或自动重试；格式错误由协议校验或客户端工具执行端报告。
- `tool_search` 仅支持 `execution="client"`。Codex 执行搜索和 MCP 调用，发现的工具定义随历史回放进入下一次请求。网关不连接 MCP 服务。
- 上游仅接受字符串 `tool_choice`。指定工具或 `allowed_tools` 会转换为过滤后的工具集合与 `required`／`auto`，不向 CodeBuddy 发送对象形式。

模型是否正确选择工具、生成有效补丁仍取决于模型能力。网关不会将普通文本伪装成工具调用。

## 验证与限制

后端测试覆盖官方 Python SDK 的 `responses.create` 和 `responses.stream`；真实 CLI 契约测试使用本地模拟上游，在临时目录执行补丁、命令、看图、MCP 搜索与调用，并检查恢复及压缩请求，不需要真实凭证：

```bash
# 安装固定基线；在项目根目录执行测试
npm install -g @openai/codex@0.153.4
CODEBUDDY_TEST_CODEX=1 CODEBUDDY_LOG_LEVEL=CRITICAL \
  venv/bin/python3 -m unittest tests.test_codex_responses_contract
```

本接口不实现 OpenAI 托管工具、后台任务、加密 reasoning、文件上传、服务端会话存储、Responses 检索／删除、远程压缩或 WebSocket。未知客户端元数据会忽略；请求了不能转换的功能会明确失败。usage 只采用上游观测，缺失时为 `null`，不是 OpenAI 计费数据。更多协议边界与真实模型验证见 [协议兼容性](协议兼容性.md)。
