# AGENTS.md

本文档只记录长期有效、仅查看局部代码容易误判的项目约定与维护陷阱。可直接从代码、依赖清单或 CI 配置得出的实现细节不在此重复；新增内容也应遵循这一原则。

## 常用命令

```bash
# 后端开发服务器
source venv/bin/activate && python3 web.py

# 前端开发服务器（代理后端 8001）
cd frontend && pnpm run dev

# 安装后端开发依赖
venv/bin/python3 -m pip install -r requirements-dev.txt

# 后端验证（unittest，行/分支覆盖率门槛 100%）
venv/bin/python3 -m coverage run -m unittest discover -s tests
venv/bin/python3 -m coverage report

# 前端完整验证
cd frontend && pnpm run format:check && pnpm run lint && pnpm run build && pnpm run test:coverage

# Docker 部署/本地构建
docker compose up -d
docker build -t codebuddy2api:local .

# 原子新增系统用户或更新密码
venv/bin/python3 scripts/hash_password.py <用户名> --output secrets/users.txt

# 使用发布镜像管理用户
docker run --rm -it -v "$PWD/secrets:/app/secrets" ghcr.io/iceean/codebuddy2api:latest add-user <用户名>
```

## 开发规定

- **测试驱动开发**：开发流程须完全遵循 TDD，保证单元测试100%覆盖、且尽可能覆盖真实用例。
- **后端测试**：使用标准库 `unittest` 与 `coverage.py`；`venv/bin/python3 -m coverage report` 对 `config.py`、`release_runtime_lock.py`、`web.py` 和 `src/` 生产代码强制执行行/分支综合 100% 覆盖率门槛。
- **前端测试**：Vitest 使用 jsdom 与 Vue Test Utils；`pnpm run test:coverage` 对 `src/` 生产代码强制执行 statements、branches、functions、lines 四项 100% 覆盖率门槛。
- **前端修改后流程**：前端修改后依次执行格式检查、lint、构建和覆盖率测试；格式检查失败时先执行 `pnpm run format`。`pnpm run build` 已包含类型检查和生产构建。
- **保证需求的正确性**：若我需要你实现的需求存在不明确的部分，请直接提问；若工作过程中出现重要的选择，停下来说明并等待回复。尽可能地不要自行推测意图和需求。
- **快速失败而不是兜底**：为保证质量、尽早发现错误，项目内各种非预期的错误应该快速失败，少对错误数据进行防御性的兜底；对外部接口行为进行适当兜底，增强兼容性。
- **干净的修改与重构**：进行 breaking change 后，无需对修改前的旧表、旧字段、旧接口等进行兼容。可以认为它们在前、后端均不再使用。
- **bug修复使用最小修改**：对于bug修复，尽量保证最小修改，同时应保证遵循上述其余原则。
- **持续更新本文档**：当开发或排错过程中出现重要或常见的的通用性问题未在此文件说明的情况，随时更新此文档。可以包括项目信息、常用命令、踩坑记录等。不要写入一次性排错过程或可从单处代码直接读出的细节。

## 架构与边界

- `web.py` 是 FastAPI/Uvicorn 入口，`config.py` 管理启动级配置；环境变量优先于硬编码默认值，安全边界配置不得由用户数据库设置覆盖。
- `CODEBUDDY_DATA_DIR` 的相对路径以 `config.py` 所在的应用根目录为基准。SQLite 和凭证路径必须由解析后的绝对数据目录派生，不能依赖进程工作目录。
- 管理台设置、API Key、凭证、模型缓存和统计都按系统用户隔离。新增管理端缓存时，隔离维度必须包含用户名；涉及凭证的数据还必须使用稳定的 `credential_id`，不得依赖列表下标。
- 管理 API 位于 `/api/admin/*`。外部协议入口遵循 `/<协议>/v1/*`，仅接受 API Key；管理台测试入口遵循 `/api/admin/playground/<协议>/v1/*`，仅接受会话 Cookie。两者复用协议处理逻辑，但不能互相接受对方的认证方式。
- `/docs`、`/redoc` 和 `/openapi.json` 只接受管理台会话 Cookie，API Key 不能替代；管理台 playground 路由不得暴露在 OpenAPI schema 中。

## HTTP、认证与浏览器安全

- Web UI 使用 HttpOnly 滑动会话 Cookie；每个系统用户最多保留 10 个会话，超限时按创建时间淘汰最旧会话。外部 API 使用 `sk-...` Bearer Token；API Key 必须是 `sk-` 加 40 字节规范无填充 Base64URL，明文只在创建时返回，SQLite 仅保存带唯一索引的 SHA-256 摘要。
- API Key 鉴权只计算一次 SHA-256 并按摘要索引查询。`last_used_at` 保存现实分钟起点的 Unix 时间戳，同一分钟最多实际更新一次，管理台仅显示到分钟。
- Cookie 鉴权成功后的续期必须在最终 ASGI 响应层完成，以覆盖错误、重定向和流式响应。私有路由使用 `PrivateNoStoreRoute`；`Cache-Control: private, no-store` 由 `ServerErrorMiddleware` 外层的最终响应中间件统一覆盖，端点中注入的临时 `Response` 不能保证最终响应头。
- 所有响应（包括 404 和未处理的 500）都由最外层 ASGI 中间件统一覆盖 CSP、`X-Frame-Options`、`X-Content-Type-Options` 和 `Referrer-Policy`，其中 `X-Frame-Options` 固定为 `DENY`。frame-ancestors 可由环境变量 `CODEBUDDY_CSP_FRAME_ANCESTORS` 配置，默认 `none`，仅允许由 `self` 和无路径的 HTTP(S) Origin 组成的列表；来源主机必须先规范化为 IDNA ASCII，再按合法 DNS 标签或 IPv6 字面量校验。CSP 来源必须在启动时严格校验，不能允许通配符、指令逃逸或不可编码的响应头字符；文档页所需的外部资源白名单与管理台同源策略分开维护。
- 请求体限制是“应用实际处理字节数”限制：先检查 `Content-Length`，再累计下游实际读取的流；不要为了验证无声明长度且端点本来不读取的请求体而主动消费整个流。登录请求使用更小的独立上限。
- `CODEBUDDY_MAX_CONCURRENT_REQUESTS=N` 表示用户可用容量 N；传给当前固定版本 Uvicorn 时必须经 `to_uvicorn_limit_concurrency()` 转成 N+1，以补偿 Uvicorn 判断时已计入当前连接的语义。
- 前端只把同时带 `WWW-Authenticate: Bearer` 的 401 视为本系统会话失效；上游 CodeBuddy 401 不得触发管理台登出。

## 数据与文件安全

- SQLite schema 创建/迁移与 `user_version` 必须在同一 `BEGIN IMMEDIATE` 事务中提交；每次连接都要确保 WAL 可用，失败即终止。
- 数据目录不得是符号链接；数据库及其 `-wal`、`-shm`、`-journal` sidecar 必须是非符号链接普通文件。数据库和凭证文件权限为 0600。
- 凭证写入使用 `O_NOFOLLOW`，加载时忽略凭证目录中的符号链接，所有文件名都必须防路径穿越。
- `CODEBUDDY_ALLOWED_API_ENDPOINTS` 是硬白名单：为空、含非法 URL 或当前端点不在其中时必须在启动阶段失败，禁止自动补入或回退。`X-Domain` 只允许 `[A-Za-z0-9.-]+`。
- 用户认证必须对不存在或无效用户执行虚拟密码哈希，避免时序枚举。密码文件只接受规范的 `pbkdf2_sha256$迭代数$盐$摘要`：默认迭代数为 600000，只允许 600000 至 1000000；盐固定 16 字节，摘要固定 32 字节，Base64URL 必须规范且无填充。修改格式时必须同步所有读写入口。

## CodeBuddy 与 OpenAI 兼容层

- CodeBuddy 上游只支持流式响应。即使客户端请求非流式，也必须用 `client.stream()` 增量消费 SSE，再由 `StreamResponseAggregator` 聚合；禁止先缓冲完整上游响应体。`RequestProcessor.prepare_request()` 必须强制注入 `stream=True`。
- 上游响应采用宽松事件提取，不在事件模型层承担完整协议验证。流式与非流式路径共享首个 choice 语义；`OpenAIStreamNormalizer` 负责拆分混合的 reasoning/content delta，并在首块补 `role: assistant`。
- 上游工具调用 ID 原样透传，只为 OpenAI 流式兼容补充缺失的 `index`，不要重新生成 ID。
- 强制推理模型会覆盖为最大推理并启用 thinking，但 `clear_thinking` 等其他客户端 `thinking` 子项必须继续透传，不能用新对象整体替换；其他模型默认开启 thinking，但客户端显式禁用时必须尊重。`CODEBUDDY_FORCED_TEMPERATURE` 非空时覆盖客户端值；模型命名空间是否剥离由 `CODEBUDDY_STRIP_MODEL_NAMESPACE` 控制。修改请求转换时注意这些优先级。
- 系统消息会通过 `src/keyword_replacer.py` 替换 Anthropic/Claude 品牌词；不要在其他层重复替换。
- 全局上游 HTTP 客户端保持 `trust_env=False`，避免环境中的 SOCKS 代理在缺少 `socksio` 时破坏服务启动。

## OAuth、凭证与模型缓存

- `src/codebuddy_auth_router.py` 只负责路由；上游认证、auth state 所属关系及 token 解析/保存放在 `src/codebuddy_oauth.py`。
- OAuth state 一旦取消或消费便不可轮询或重放。上游登录 URL 只允许带主机、无 userinfo/控制字符的绝对 HTTP(S) URL；校验通过前不得登记 state 或让前端导航。成功响应不能向浏览器返回 token 或用户信息；保存失败应返回 500，且不得恢复已消费 state。
- 手动添加和 OAuth 保存必须共用 token 解析：`user_id` 优先取 JWT `sub`；失败或缺失时使用真实 CLI 兼容的 `anonymous_<token 后 8 位>`，不得读取 `ACC_USER_ID` 或 `ACC_USER_NICKNAME`。
- 手动添加的 bearer-only 凭证不要求 `account_uid`、账号列表、过期时间或 refresh token，且不得进入 OAuth 刷新或账号切换流程；请求头中的 `X-User-Id` 对 OAuth 凭证优先使用 `account_uid`，否则继续使用 `user_id`。
- OAuth 登录由后端返回 `interval` 与 `expires_in` 并由前端原样遵守；敏感的分阶段登录进度只能保存在服务端。凭证文件除规范字段外还需保存各阶段完整上游 JSON 响应体，用于后续兼容，但管理 API 不得返回这些原始响应。
- OAuth 自动刷新只由启动任务和每小时任务触发，在 `expires_at - 86400` 秒进入刷新窗口；聊天、模型发现等请求路径不得触发、等待或重试刷新。
- 每个系统用户拥有独立凭证目录和 Token 管理器。凭证轮换开关是用户级设置，轮换频率必须为正整数。
- CodeBuddy `/v3/config` URL、`Host` 和 `X-Domain` 必须由同一个当前 API endpoint 派生。
- 模型缓存键至少包含系统用户与 `credential_id`。凭证过期、删除或失效时必须同时驱逐缓存并作废在途查询；旧请求结果不能写回，也不能被同 ID 的新凭证复用。过期值不得作为失败回退，并发未命中使用 per-key single-flight 合并。

## 统计系统

- 统计按系统用户隔离，区分外部 API、管理台 playground 和凭证测试。写入必须从 ASGI 事件循环卸载；写入失败不能影响聊天响应，但要记录日志并增加进程级 `dropped_events`。运行期数据库丢失必须快速失败，不能返回伪造的空统计。
- 逐请求脱敏明细只保留 90 天，UTC 小时汇总永久保留；清理由启动任务和独立定时任务按索引分批执行，不能依赖后续写入触发。
- 查询在同步 FastAPI 路由线程池中执行，并在 SQL 中有界聚合。浏览器 IANA 时区的非整点范围和本地日历边界，在保留期内用明细修正边界小时；过期历史只能按小时近似，必须通过 `boundary_precision` 明示。
- 请求列表使用成对的 `snapshot_id`、`snapshot_time` 做稳定的页码分页；详情必须沿用列表快照。拒绝未来快照；清理水位越过快照可用下界后要求重新获取第一页。
- 永久模型维度只采用配置或成功上游响应确认的规范模型，未确认值记为 `unknown`。缺失 usage 保持 `null`，不能当作 0；覆盖率以 `total_tokens` 是否已知计算。延迟超过直方图上限时进入显式 overflow 桶。
- 永远不要保存提示词、回答、请求头、Bearer/CodeBuddy Token、工具参数、原始错误体或会话 ID。模型、错误类型、思考模式和结束原因写入前必须归一化到受控值。统计不承担 billing 套餐、余额或货币换算。

## 前端约定

- 管理数据的 Vue Query key 必须以 `['admin', username, ...]` 开头。登出、本地会话 401 或用户名变化时，同时清空 Query Cache 和 Mutation Cache。
- 查询和 mutation 使用 `networkMode="always"`，查询禁用 `refetchOnReconnect`，保证离线时立即失败且联网后不补发。手动刷新和重试统一使用 `RefreshButton`；它需在 refetch 前检查离线状态，并独立维持最短加载反馈，不能只依赖 `isFetching`。
- 可聚焦元素不要使用 Tailwind `transition-colors`；它会一并过渡 `outline-color`，使暗色模式的键盘焦点轮廓从浏览器默认浅色短暂闪烁。应使用 `transition-[color]` 或 `transition-[color,background-color]` 等明确的过渡属性。
- 主题动画只在根节点维护一个数值进度，所有动画语义色由该进度派生。不要为后代递归添加颜色 transition，也不要用 `dark:` 在两个动画语义变量间切换。需单调变化的颜色使用等效不透明端点，避免透明色插值泛白；连续主题切换必须从当前进度反向，路由切换期间禁止启动主题切换。
- 内容哈希的静态资源长期 `immutable`，入口 HTML 为 `no-store`，未哈希资源必须重新验证。缺少 `frontend/dist/index.html` 时快速失败，不提供单文件回退。
- 前端开发/构建要求 Node.js 24.11+。Vite 8/Rolldown 手动分包使用 `rollupOptions.output.codeSplitting.groups`，不要恢复对象形式 `manualChunks`；TypeScript 配置保留 `vite/client` 类型。

## Docker 与发布

- 容器入口必须先以 root 准备挂载目录和用户文件副本，再通过 `gosu` 切换到 UID 1001 的 `appuser`。不要用 Compose `user` 或 `docker run --user` 绕过入口准备。
- 运行时挂载 `./data` 和只读 `./secrets`；入口固定数据目录为 `/app/data`，并将宿主 `users.txt` 复制成运行时私有只读文件。服务启动后修改用户文件必须重启容器才会生效。
- 用户文件以同目录临时文件原子替换，不支持符号链接、非普通文件或多硬链接；重复用户名会替换全部旧记录，并发写入不提供锁。
- 发布只接受稳定语义版本 tag。tag、`web.py` 的 `APP_VERSION`、`frontend/package.json` 版本及 `CHANGELOG.md` 对应版本必须一致。
- 发布镜像必须同时支持 `linux/amd64`、`linux/arm64` 和 `linux/arm/v7`，构建时生成 SBOM/provenance，并使用 Cosign keyless signing 对最终镜像 digest 签名。发布顺序必须保持“完整验证 → 多架构按 digest 推送 → 每个架构漏洞扫描 → 对 digest 加版本/`latest` tag → 签名与 GitHub Release”；任一架构存在 `CRITICAL` 漏洞都应阻断发布。Trivy 默认包含尚无修复版本的漏洞，手动发布仅可通过 `ignore_unfixed=true` 忽略这类漏洞。只有最高稳定版本更新 `latest`。
- 发布归档必须可复现：使用 tag commit 时间，规范成员顺序、时间、权限和 owner 元数据；只收录生产文件，拒绝输入路径中的符号链接及其他非普通文件。输出目录不能位于任何输入目录内，所有产物先在临时目录完整生成再原子替换，checksum 最后发布。
- 本地 Release 更新器必须从自身所在的 `scripts` 目录解析项目根目录，不能依赖调用时工作目录；只能在非 Git 的 Release 安装目录中由项目外系统 Python 执行。Release 服务与更新/回滚共用项目根目录的 `.codebuddy2api-runtime.lock` 独占锁，Git 开发环境和 Docker 不启用该锁；锁文件不得进入完整备份，也不得在部署或恢复时删除、替换。`--yes` 只能跳过交互确认，不能绕过锁。Release 清单必须与归档成员完全一致，本地包仅接受以 `codebuddy2api` 开头的 `.zip` 或 `.tar.gz`。完整备份固定为项目根目录下的 `.update-backups/latest`，正常完成更新或回滚后只能保留这一份完整备份，且备份时必须排除 `.update-backups` 自身。回滚提交后的残留备份清理失败不能反转事务结果或报告回滚失败；必须报告回滚已经成功、列出残留路径并提醒用户不要重试回滚。更新默认重建 `venv`；`--reuse-venv` 只接受 `pyvenv.cfg` 中唯一且明确设置 `include-system-site-packages = false` 的环境，必须确保 pip 至少为 23.0 并验证安装报告版本为稳定的 `1`，再用全新解析报告确定新版完整依赖闭包，只保留闭包和 `pip`、`setuptools`、`wheel`，清理其余包并通过 `pip check`，任何失败都必须触发完整快照恢复。
