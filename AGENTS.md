# AGENTS.md

## 常用命令

```bash
# 开发服务器（需要 venv）
source venv/bin/activate && python3 web.py

# 前端开发服务器（代理后端 8001）
cd frontend && pnpm run dev

# 构建新版前端静态产物（可选；缺失时 / 回退到旧前端）
cd frontend && pnpm run build

# Docker 部署
docker compose up -d

# 本地构建 Docker 镜像
docker build -t codebuddy2api:local .

# 安装开发依赖
venv/bin/python3 -m pip install -r requirements-dev.txt

# 后端验证（unittest + 行/分支覆盖率 100% 门槛）
venv/bin/python3 -m coverage run -m unittest discover -s tests
venv/bin/python3 -m coverage report

# 前端修改后验证
cd frontend && pnpm run format:check && pnpm run lint && pnpm run build && pnpm run test:coverage

# 原子新增系统用户或更新已有用户密码
venv/bin/python3 scripts/hash_password.py <用户名> --output secrets/users.txt

# 使用发布镜像新增系统用户或更新已有用户密码
docker run --rm -it -v "$PWD/secrets:/app/secrets" ghcr.io/iceean/codebuddy2api:latest add-user <用户名>
```

## 架构

- **入口点**: `web.py` — FastAPI 应用，使用 **Uvicorn** 提供服务，本地通过 `uvicorn.run()` 启动。
- **配置**: `config.py` — 启动级配置优先级为：环境变量 > 硬编码默认值，日志级别等服务配置不支持运行时修改。`CODEBUDDY_DATA_DIR` 的相对路径始终以应用根目录（`config.py` 所在目录）为基准，SQLite 与 CodeBuddy 凭证必须从该绝对数据目录派生，不得依赖进程工作目录。管理台设置按本系统用户隔离，首次保存后持久化到 `data/codebuddy2api.sqlite3`；未保存的用户使用当前系统默认配置，安全边界配置不会从数据库加载。
- **SQLite 存储**: `src/sqlite_database.py` 维护 schema 版本、WAL、事务和 0600 权限；首次建表与 `user_version` 在同一 `BEGIN IMMEDIATE` 事务中提交。每次连接都检查并恢复 WAL，无法启用时快速失败；数据目录不得为符号链接，数据库文件及 `-wal`、`-shm`、`-journal` sidecar 必须是非符号链接普通文件。`src/user_settings_store.py` 保存用户设置，`src/api_key_store.py` 保存 API Key 的 SHA-256 摘要。服务启动时自动创建数据库及空表。
- **认证**: 实际实现拆分在 `src/auth_router.py`、`src/users_store.py`、`src/api_key_store.py`、`src/session_store.py` 和 `src/auth_types.py`。生产代码应直接从这些真实模块导入，不再通过 `src/auth.py` 兼容层转导出。认证层包含两个独立入口：
   - Web UI 登录 → HttpOnly 会话 Cookie（7 天有效期），通过 `secrets/users.txt`（PBKDF2 哈希，每行格式 `用户名:哈希`）验证。
   - API 访问 → 通过 Web UI 生成的 `sk-...` Bearer Token。SQLite 仅保存带唯一索引的 SHA-256 摘要，不保存明文 Key。
   - API Key 必须是 `sk-` 加 40 字节规范无填充 Base64URL；鉴权只计算一次 SHA-256 并按摘要索引查询。`last_used_at` 存储现实分钟起点的 Unix 时间戳，同一分钟最多实际更新一次，管理台仅显示到分钟。
   - 两类鉴权入口的最终响应统一设置 `Cache-Control: private, no-store`，覆盖鉴权失败、畸形请求体、参数校验错误、`HTTPException`、未处理的 500、流式响应及自动斜杠重定向；登录、登出及 OAuth 回调同样受保护。私有路由必须使用 `PrivateNoStoreRoute`，由位于 `ServerErrorMiddleware` 外层的最终 ASGI 响应中间件根据路由匹配标记统一执行，端点不得依赖注入的临时 `Response` 设置此头。
- **Web 管理台**: 前端位于 `frontend/`，`/` 优先使用 Vue 3 + TypeScript + Vite + 自建 UI 组件 + Pinia + Vue Query 新管理台；若 `frontend/dist/index.html` 不存在，`/` 回退到 `frontend/admin.html` 旧单文件管理台。`/admin` 始终使用旧管理台。对于功能变化，需要修改时须一同修改两个管理台；优化类则不用（旧管理台仅保证最小支持）。旧管理台保持实现同步即可，不新增读取 `frontend/admin.html` 具体内容的测试。开发态使用 `pnpm run dev`，Vite 将 `/auth`、`/api`、`/codebuddy`、`/openai`、`/health`、`/docs`、`/redoc` 和 `/openapi.json` 代理到 `127.0.0.1:8001`。
- **前端工具链**: Vite 8 使用 Rolldown，手动分包必须使用 `rollupOptions.output.codeSplitting.groups`，不再支持对象形式的 `manualChunks`。TypeScript 6 默认检查副作用导入，`tsconfig.json` 必须包含 `vite/client` 类型。前端开发和构建要求 Node.js 24.11+。
- **协议入口**: OpenAI 兼容处理集中在 `src/openai_router.py`。外部客户端使用 `/openai/v1/*`，仅接受 `sk-...` API Key；管理台测试使用 `/api/admin/playground/openai/v1/*`，仅接受会话 Cookie。两组入口必须复用相同协议处理逻辑，但不得互相接受对方的认证方式。新增协议时遵循外部 `/<协议>/v1/*`、管理台测试 `/api/admin/playground/<协议>/v1/*` 的命名规则。
- **开发文档**: `/docs`、`/redoc` 和 `/openapi.json` 仅接受管理台会话 Cookie，未登录时返回带 `WWW-Authenticate: Bearer` 的 401，外部 API Key 不可替代会话；三个端点的成功和鉴权失败响应都必须设置 `Cache-Control: private, no-store`。OpenAPI 使用 `ApiKeyBearer` 描述外部 `/openai/v1/*`，使用 `SessionCookie` 描述公开 schema 中受会话保护的管理接口。Chat Completions 的 `messages` 必须为非空数组且每项必须包含 `role` 和 `content`；仅带非空对象数组 `tool_calls` 的 assistant 消息可省略 `content`。隐藏 `/api/admin/playground/openai/v1/*`。Swagger 禁用外部 schema 验证器和授权持久化。
- **管理 API**: 管理台专用接口集中在 `src/admin_router.py` 并挂载到 `/api/admin/*`。凭证管理使用稳定 `credential_id`，不要在前端或新 API 中依赖凭证列表 index；手动添加凭证的 `bearer_token` 在 Pydantic 模型层去除首尾空白并拒绝空值。
- **CodeBuddy OAuth**: `src/codebuddy_auth_router.py` 只保留 FastAPI 路由；上游认证客户端、auth_state 所属关系和 token 解析/保存位于 `src/codebuddy_oauth.py`。认证可通过会话保护的 `POST /codebuddy/auth/cancel` 取消，state 被取消或消费后不可继续轮询或重放。轮询成功仅向浏览器返回保存结果，不得返回 access token、refresh token 或用户信息；凭证保存失败必须返回 500，且已消费的 state 不得恢复。手动添加与 OAuth 保存凭证共用 token 解析：`user_id` 优先取 JWT `sub`，解析失败或无 `sub` 时按真实 CLI token fallback 使用 `anonymous_<token 后 8 位>`，不读取 `ACC_USER_ID` 或 `ACC_USER_NICKNAME`。
- **凭证隔离**: 每个系统用户在 `data/credentials/users/<哈希目录名>/` 下拥有独立的 CodeBuddy Token 目录。Token 管理器按用户单例化（`CodeBuddyTokenManagerRegistry`）；磁盘安全读写位于 `src/credential_store.py`，过期判断和轮换策略位于 `src/credential_rotation.py`。凭证轮换开关为用户级设置 `CODEBUDDY_AUTO_ROTATION_ENABLED`，轮换频率 `CODEBUDDY_ROTATION_COUNT` 必须为正整数。
- **上游 API**: CodeBuddy 仅支持流式响应。客户端的非流式请求也必须通过 `client.stream()` 增量读取上游 SSE，再由 `StreamResponseAggregator` 聚合，禁止先缓冲完整响应体。所有请求体在 `RequestProcessor.prepare_request()` 中强制注入 `stream=True`。

## 特殊约定与注意事项

- **强制推理**: `CODEBUDDY_FORCED_REASONING_MODELS` 中的模型会被强制设置 `reasoning_effort="max"` 及 `thinking.type="enabled"`；默认包含 `deepseek-v4-pro`、`deepseek-v4-flash`、`glm-5.1` 和 `glm-5.2`。其他 `thinking` 子项（如 `clear_thinking`）按客户端请求透传。
- **默认思考开关**: 未命中强制推理模型列表的请求默认补传 `enable_thinking=true`；若客户端显式传入 `enable_thinking=false` 或 `thinking.type="disabled"`，则不补。
- **强制 temperature**: `CODEBUDDY_FORCED_TEMPERATURE` 默认为 `1`，会覆盖客户端传入值；留空则不覆盖。
- **模型名前缀处理**: `CODEBUDDY_STRIP_MODEL_NAMESPACE` 默认为 `true`，会把 `provider/model` 形式的模型名在转发上游前改为 `model`；留空或 `false` 则不处理。
- **关键词替换**: 系统消息中自动将 Anthropic/Claude 相关引用替换为 Tencent/CodeBuddy（`src/keyword_replacer.py`）。
- **工具调用 ID 兼容**: 透传上游工具调用 ID，仅通过 `add_openai_tool_call_indexes()` 为流式响应补齐 OpenAI 客户端需要的 `index`。
- **响应标准化**: 流式和非流式路径通过 `CodeBuddyResponseEvent` 宽松提取共享的首个 choice 响应语义，不承担上游协议验证。`OpenAIStreamNormalizer` 再将混合的 `reasoning_content`+`content` delta 拆分为独立块，并在首个块中注入 `role: assistant`。
- **HTTP 客户端代理**: 全局上游 HTTP 客户端设置 `trust_env=False`，避免本机 `ALL_PROXY`/`HTTP_PROXY` 指向 SOCKS 代理但未安装 `socksio` 时导致服务启动失败。
- **端点前缀**: 外部聊天 API 路径为 `POST /openai/v1/chat/completions`，客户端应将 `base_url` 设置为包含 `/openai/v1`；管理台测试入口为 `/api/admin/playground/openai/v1`。
- **管理台离线请求**: Vue Query 的查询和 mutation 全局使用 `networkMode="always"`，查询同时禁用 `refetchOnReconnect`，确保断网时立即失败而不是暂停排队，禁止恢复联网后补发读取或写入操作。所有手动刷新和错误重试统一使用 `RefreshButton`；该组件在调用 `refetch()` 前检查离线状态，并独立维护至少 300ms 的按钮加载状态，不能只依赖 `isFetching`。
- **文件安全**: 凭证写入使用 `O_NOFOLLOW` + 0600 权限。加载时跳过 `data/credentials/` 中的符号链接。文件名经过路径穿越清理。SQLite 数据库拒绝符号链接路径并强制设置 0600 权限。

## 开发规定

- **测试驱动开发**：开发流程须完全遵循 TDD，保证单元测试100%覆盖、且尽可能覆盖真实用例。
- **后端测试**：使用标准库 `unittest` 与 `coverage.py`；`venv/bin/python3 -m coverage report` 对 `config.py`、`web.py` 和 `src/` 生产代码强制执行行/分支综合 100% 覆盖率门槛。
- **前端测试**：Vitest 使用 jsdom 与 Vue Test Utils；`pnpm run test:coverage` 对 `src/` 生产代码强制执行 statements、branches、functions、lines 四项 100% 覆盖率门槛。
- **前端修改后流程**：前端文件修改后先运行 Prettier 检查，失败时执行 `pnpm run format` 并重新检查；随后依次运行 `pnpm run lint`、`pnpm run build`、`pnpm run test:coverage`。`pnpm run build` 包含类型检查和生产构建；CI 单独运行 `pnpm run typecheck` 与 `pnpm run build:bundle`。
- **保证需求的正确性**：若我需要你实现的需求存在不明确的部分，请直接提问；若工作过程中出现重要的选择，停下来说明并等待回复。尽可能地不要自行推测意图和需求。
- **快速失败而不是兜底**：非常重要！目前项目仍在开发中。为保证质量、尽早发现错误，各种非预期的错误应该快速失败，少对错误数据进行防御性的兜底。
- **干净的修改与重构**：进行 breaking change 后，无需对修改前的旧表、旧字段、旧接口等进行兼容。可以认为它们在前、后端均不再使用。
- **bug修复使用最小修改**：对于bug修复，尽量保证最小修改，同时应保证遵循上述其余原则。
- **持续更新本文档**：当开发或排错过程中出现重要或常见的的通用性问题未在此文件说明的情况，随时更新此文档。可以包括项目信息、常用命令、踩坑记录等。


## Docker

- 容器入口以 root 完成挂载目录准备，将宿主 `users.txt` 复制为 `/run/codebuddy2api/users.txt` 的 `appuser` 私有只读副本，再使用 `gosu` 以非 root 用户 `appuser`（UID 1001）运行服务；不要通过 Compose `user` 或 `docker run --user` 跳过启动准备。
- 挂载卷：`./data`、`./secrets:ro`；SQLite 和 CodeBuddy 凭证统一保存在 `data/`。Compose 默认使用 `ghcr.io/iceean/codebuddy2api:latest` 发布镜像，`.env` 为可选覆盖文件。Compose 和 `entrypoint.sh` 强制 `CODEBUDDY_DATA_DIR=/app/data`，入口脚本同时强制 `CODEBUDDY_USERS_FILE=/run/codebuddy2api/users.txt`。
- 用户文件通过同目录临时文件原子替换，自动补齐缺失的末尾换行并保留或收紧 UID/GID 与 POSIX 权限；不支持符号链接、非普通文件和多硬链接，不保证保留 ACL、扩展属性或自定义安全标签。重复用户名会删除全部旧记录后写入新密码；并发写入不提供锁。服务运行后修改用户文件需执行 `docker compose restart codebuddy2api` 刷新运行时副本。
- Dockerfile 固定使用 Node 24.11.1 构建前端，再将 `frontend/dist/` 与明确列出的后端运行文件复制到 Python 3.12 运行时镜像；镜像内提供 `hash-password` 和 `add-user` 辅助命令。
- 容器 CMD 为 `uvicorn web:app --host 0.0.0.0 --port 8001 --no-access-log`。
- Release workflow 只发布 `v数字.数字.数字` 稳定 tag。发布标签必须等于 `v{web.py 的 APP_VERSION}`，且 `frontend/package.json` 版本必须与 `APP_VERSION` 相同；三方不一致时在构建前快速失败。发布前必须在 `CHANGELOG.md` 添加对应版本二级标题及非空说明；workflow 会先跑后端和前端验证，再按 digest 推送一次包含 `linux/amd64`、`linux/arm64` 和 `linux/arm/v7` 的多架构镜像，并对同一 digest 的每个架构分别执行 Trivy 漏洞扫描，任一架构存在 `CRITICAL` 漏洞都会阻断发布。Trivy 默认忽略尚无修复版本的漏洞，手动发布可通过 `ignore_unfixed=false` 将其纳入扫描。扫描通过后才为该 digest 添加版本 tag；仅最高稳定版本更新容器和 GitHub Release 的 `latest`。镜像构建时生成 SBOM/provenance，并使用 Cosign keyless signing 对镜像 digest 签名，最后创建或更新 GitHub Release。个人 GHCR 包首次推送后默认为 private，需在包出现后手动改为 Public；当前工作流全程使用认证访问，因此无需仅为可见性重跑。自动发布说明通过 `.github/release.yml` 将 PR 分为新功能、Bug 修复和其他变更；workflow 同时通过 GitHub API 排除有关联 PR 的 commit，再将独立提交按 `feat`、`fix` 和其他 Conventional Commit 前缀分类，并在各分类内从旧到新排列。最终变更记录依次显示“独立提交”和“合并的 PR”，空来源与空分类不显示，唯一的 `Full Changelog` 固定置底。独立提交中使用 `chore(release):` 或 `chore(release)!:` 前缀的发布提交不会进入变更记录；PR 发布不应用此过滤规则。

## 安全边界

- `CODEBUDDY_ALLOWED_API_ENDPOINTS` 是硬白名单，非白名单内的 API 端点会回退到默认值。
- `X-Domain` 请求头经过 `[A-Za-z0-9.-]+` 正则过滤，防止请求头注入。
- 对不存在/无效的用户执行虚拟密码哈希验证，防止基于响应时间的用户枚举。
- PBKDF2 密码哈希默认使用 600000 次迭代，只接受规范的 `pbkdf2_sha256$迭代数$盐$摘要`：迭代数范围 600000 至 1000000，盐固定 16 字节、摘要固定 32 字节，Base64URL 必须无填充且编码规范。
- 前端仅将带 `WWW-Authenticate: Bearer` 的 401 视为本系统会话失效；CodeBuddy 上游凭证 401 不得触发管理会话注销。
- 文档鉴权验收必须覆盖 `/docs`、`/redoc`、`/openapi.json` 的无会话 401、有效会话成功及 API Key 被拒绝，并断言 schema 的外部 Bearer 安全声明、Chat 请求体和隐藏入口。
