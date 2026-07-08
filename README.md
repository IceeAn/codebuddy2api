<h1 align="center">
  <img src="frontend/public/assets/codebuddy2api.svg" alt="CodeBuddy2API 图标" height="32" style="height: 1em; width: 1em; vertical-align: -0.12em;">
  CodeBuddy2API
</h1>

<p align="center">
  将 CodeBuddy 上游服务封装为 OpenAI Chat Completions 兼容接口，并提供多用户管理台、API Key、上游凭证隔离和自动轮换能力。
</p>

<p align="center">
  <a href="#本地快速开始">本地快速开始</a> ·
  <a href="#docker-部署">Docker 部署</a> ·
  <a href="#客户端使用">客户端使用</a>
</p>

> [!WARNING]
> 本仓库代码通过 AI 生成，未经过严格的人工代码审查或安全审计，不能保证部署环境的安全性。建议仅在本地、内网使用；如确需公网部署，建议反向代理鉴权、IP 白名单等额外保护。不建议将本服务直接暴露在公网。

## 支持范围

- `POST /openai/v1/chat/completions`：兼容 OpenAI Chat Completions，支持流式和非流式客户端请求。
- `GET /openai/v1/models`：返回当前用户可用的模型列表。
- CodeBuddy 上游只提供流式响应；非流式客户端请求由本服务聚合后返回。
- 当前不提供 Responses、Embeddings、Images、Audio 等其他 OpenAI API。
- `/api/admin/playground/openai/v1/*` 与 `/openai/v1/*` 使用相同协议处理逻辑，但只供管理台通过会话 Cookie 测试，不是外部客户端入口。

## 认证模型

本项目包含三种用途不同的凭证：

| 凭证             | 用途                          | 存储位置                                             |
| ---------------- | ----------------------------- | ---------------------------------------------------- |
| 系统用户名和密码 | 登录 Web 管理台               | `secrets/users.txt`，密码使用 PBKDF2 哈希            |
| `sk-...` API Key | 外部客户端调用 `/openai/v1/*` | `data/codebuddy2api.sqlite3`，只保存 SHA-256 摘要    |
| CodeBuddy 凭证   | 本服务访问 CodeBuddy 上游     | `.codebuddy_creds/users/<用户目录>/`，按系统用户隔离 |

Web 管理台使用 7 天有效的 `HttpOnly` 会话 Cookie。外部 API 不接受会话 Cookie、Basic Auth 或 Bearer `用户名:密码`；必须使用管理台生成的 `sk-...` API Key。

## 本地快速开始

### 前置要求

- Python 3.10 或更高版本
- Git
- 可选：Node.js 24.11+ 与 pnpm 10.29.2+，用于构建新版管理台

PowerShell 示例使用 Python Launcher `py -3` 创建虚拟环境，若未安装 Python Launcher，请改用本机可用的 Python 3.10+ 命令。

### 1. 安装

macOS / Linux bash：

```bash
git clone https://github.com/iceean/codebuddy2api.git
cd codebuddy2api

python3 -m venv venv
source venv/bin/activate
python3 -m pip install -r requirements.txt
```

Windows PowerShell：

```powershell
git clone https://github.com/iceean/codebuddy2api.git
cd codebuddy2api

py -3 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. 创建配置和系统用户

macOS / Linux bash：

```bash
cp .env.example .env
mkdir -p secrets
python3 scripts/hash_password.py admin >> secrets/users.txt
chmod 600 secrets/users.txt
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
New-Item -ItemType Directory -Force secrets | Out-Null
$userLine = & .\venv\Scripts\python.exe scripts\hash_password.py admin
[System.IO.File]::AppendAllText("secrets\users.txt", $userLine + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
```

Windows 原生环境不执行 `chmod`，如需收紧权限请使用 NTFS ACL。

`scripts/hash_password.py` 会提示输入密码，并输出一行使用 600,000 次 PBKDF2-HMAC-SHA256 的 `用户名:密码哈希`。重复执行可添加多个系统用户。

不建议公网部署，但若需公网部署必须把实际 IP 或域名加入 `.env`：

```dotenv
CODEBUDDY_ALLOWED_HOSTS=api.example.com,127.0.0.1,localhost
```

只有浏览器需要跨域调用外部 `/openai/v1/*` 时才配置 `CODEBUDDY_ALLOWED_ORIGINS`；会话 Cookie 不允许跨域携带，管理台应保持同源访问。

### 3. 可选：构建新版管理台

macOS / Linux bash 与 Windows PowerShell：

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm run build
cd ..
```

若 `frontend/dist/index.html` 不存在，根路径 `/` 会回退到旧版单文件管理台；`/admin` 始终提供旧版管理台。
旧版管理台当前仅提供基础支持，建议使用新版管理台。

### 4. 启动并初始化

macOS / Linux bash：

```bash
source venv/bin/activate
python3 web.py
```

Windows PowerShell：

```powershell
.\venv\Scripts\python.exe web.py
```

浏览器访问 `http://127.0.0.1:8001`，然后：

1. 使用刚创建的系统用户名和密码登录。
2. 在“凭证管理”中启动 CodeBuddy 认证并完成官方登录授权，也可以手动添加凭证。
3. 确认凭证列表中至少有一个有效凭证。
4. 在“API 密钥”中创建一个 `sk-...` API Key；明文只会在创建时返回一次。

### 5. 发起第一个请求

macOS / Linux bash：

```bash
curl "http://127.0.0.1:8001/openai/v1/chat/completions" \
  -H "Authorization: Bearer sk-your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "glm-5.2",
    "messages": [
      {"role": "user", "content": "你好，2+2 等于几？"}
    ]
  }'
```

Windows PowerShell：

```powershell
$body = @{
  model = "glm-5.2"
  messages = @(
    @{
      role = "user"
      content = "你好，2+2 等于几？"
    }
  )
} | ConvertTo-Json -Depth 4

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8001/openai/v1/chat/completions" `
  -Method Post `
  -Headers @{ Authorization = "Bearer sk-your_api_key" } `
  -ContentType "application/json" `
  -Body $body
```

## Docker 部署

### 1. 准备挂载文件

```bash
cp .env.example .env
mkdir -p secrets data .codebuddy_creds
python3 scripts/hash_password.py admin >> secrets/users.txt
chmod 644 secrets/users.txt
```

容器以 UID 1001 的 `appuser` 运行，且 `users.txt` 以只读方式挂载，因此容器用户必须能够读取该文件。Linux 主机若需要更严格的权限，可将文件所有者设置为 UID 1001 后执行：

```bash
sudo chown 1001:1001 secrets/users.txt
chmod 400 secrets/users.txt
```

### 2. 配置并启动

编辑 `.env`，至少把公网域名加入 Host 白名单：

```dotenv
CODEBUDDY_ALLOWED_HOSTS=api.example.com,127.0.0.1,localhost
CODEBUDDY_SSL_VERIFY=true
```

```bash
docker compose up -d --build
```

Docker 镜像会构建新版管理台，并把以下目录挂载到容器：

- `./data`：SQLite 用户设置和 API Key 摘要。
- `./.codebuddy_creds`：CodeBuddy 凭证。
- `./secrets/users.txt`：只读系统用户文件。

Docker Compose 和容器入口都会强制设置 `CODEBUDDY_DATA_DIR=/app/data`，确保数据库始终写入 `./data` 持久化挂载；`.env` 中的同名配置仅用于非容器本地运行。

## 客户端使用

兼容大部分支持允许自定义端点 URL 且支持 OpenAI Chat Completions 协议的客户端。

手动调用示例如下（须将 `sk-your_api_key` 更换为上文获取的API Key）：

```python
import openai

client = openai.OpenAI(
    api_key="sk-your_api_key",
    base_url="http://127.0.0.1:8001/openai/v1",
)

# 非流式请求
response = client.chat.completions.create(
    model="glm-5.2",
    messages=[{"role": "user", "content": "你好"}],
)
print(response.choices[0].message.content)

# 流式请求
stream = client.chat.completions.create(
    model="glm-5.2",
    messages=[{"role": "user", "content": "写一个 Python Hello World"}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")
```

## 端点与鉴权边界

| 调用方     | 路径                                  | 鉴权方式                | 说明                               |
| ---------- | ------------------------------------- | ----------------------- | ---------------------------------- |
| 外部客户端 | `/openai/v1/*`                        | `sk-...` Bearer API Key | 对外 OpenAI 兼容入口               |
| Web 管理台 | `/api/admin/playground/openai/v1/*`   | 会话 Cookie             | 管理台 OpenAI 测试入口，不接受 API Key |
| Web 管理台 | `/auth/*`                             | 登录接口或会话 Cookie   | 登录、恢复会话、退出               |
| Web 管理台 | `/api/admin/*`                        | 会话 Cookie             | 凭证、API Key、设置和状态管理      |
| 开发文档   | `/docs`、`/redoc`、`/openapi.json` | 会话 Cookie | Swagger、ReDoc 与 OpenAPI schema   |
| 监控系统   | `GET /health`     | 无                      | 健康检查                           |

登录新版管理台后，可从“开发文档”页面的按钮在新标签页打开 `/docs`；也可以在保持登录会话的浏览器中直接访问 `/docs` 或 `/redoc`。未登录请求会返回 401，`sk-...` API Key 不能代替管理台会话访问文档。

OpenAPI 文档会展示外部 `/openai/v1/*` 的 Bearer API Key 鉴权和 Chat Completions 请求体。使用 Swagger 调试外部接口时，仍需通过 Authorize 填写管理台生成的 `sk-...` API Key。管理台测试入口 `/api/admin/playground/openai/v1/*` 不会出现在 schema 中。

管理台测试入口按 `/api/admin/playground/<协议>/v1/*` 扩展。例如以后增加 Anthropic 协议时，外部入口使用 `/anthropic/v1/*`，对应的管理台测试入口使用 `/api/admin/playground/anthropic/v1/*`。

## 配置模型

配置分为两类：

1. 启动与安全边界配置：环境变量优先于代码默认值，只在服务启动时加载，不从 SQLite 读取。
2. 用户级运行配置：管理台首次保存后按用户名写入 `data/codebuddy2api.sqlite3`；未保存的字段继承对应环境变量或代码默认值。

以下表格反映当前配置；`config.py` 是默认值的权威来源，`.env.example` 是部署模板，不穷举所有用户级设置。

### 启动与安全边界配置

| 环境变量                          | 默认值                        | 说明                                                    |
| --------------------------------- | ----------------------------- | ------------------------------------------------------- |
| `CODEBUDDY_USERS_FILE`            | `secrets/users.txt`           | 系统用户文件路径                                        |
| `CODEBUDDY_HOST`                  | `127.0.0.1`                   | 本地启动监听地址                                        |
| `CODEBUDDY_PORT`                  | `8001`                        | 本地启动监听端口                                        |
| `CODEBUDDY_API_ENDPOINT`          | `https://copilot.tencent.com` | CodeBuddy 上游；国际站可使用 `https://www.codebuddy.ai` |
| `CODEBUDDY_ALLOWED_API_ENDPOINTS` | 中国站、国际站                | 可接收真实 CodeBuddy Token 的上游白名单                 |
| `CODEBUDDY_CREDS_DIR`             | `.codebuddy_creds`            | 上游凭证根目录                                          |
| `CODEBUDDY_DATA_DIR`              | `data`                        | 运行数据目录，包含 SQLite；Docker 固定为 `/app/data`    |
| `CODEBUDDY_ALLOWED_HOSTS`         | `localhost,127.0.0.1`         | 允许访问本服务的 Host 头                                |
| `CODEBUDDY_ALLOWED_ORIGINS`       | 空                            | 允许跨域访问的浏览器 Origin；空表示不启用 CORS          |
| `CODEBUDDY_SSL_VERIFY`            | `true`                        | 上游 TLS 证书校验；公网部署必须保持开启                 |
| `CODEBUDDY_LOG_LEVEL`             | `INFO`                        | `DEBUG`、`INFO`、`WARNING`、`ERROR` 或 `CRITICAL`       |

`CODEBUDDY_API_ENDPOINT` 不在白名单内时会记录错误并回退到默认中国站，防止真实 Token 被转发到未授权地址。

### 用户级运行配置

| 环境变量默认值                      | 默认值                                              | 说明                                       |
| ----------------------------------- | --------------------------------------------------- | ------------------------------------------ |
| `CODEBUDDY_MODELS`                  | `glm-5.2,deepseek-v4-pro`                           | 与 CodeBuddy 动态模型列表合并的附加模型    |
| `CODEBUDDY_FORCED_REASONING_MODELS` | `deepseek-v4-pro,deepseek-v4-flash,glm-5.1,glm-5.2` | 强制启用最大推理参数的模型；空表示关闭     |
| `CODEBUDDY_FORCED_TEMPERATURE`      | `1`                                                 | 强制覆盖 `temperature`；空表示保留客户端值 |
| `CODEBUDDY_STRIP_MODEL_NAMESPACE`   | `true`                                              | 将 `provider/model` 转换为 `model`         |
| `CODEBUDDY_AUTO_ROTATION_ENABLED`   | `true`                                              | 是否自动轮换 CodeBuddy 凭证                |
| `CODEBUDDY_ROTATION_COUNT`          | `1`                                                 | 每 N 次请求切换凭证，必须为正整数          |

## 架构概览

```text
外部客户端 ── /openai/v1 + API Key ──────────────────┐
                                                     ├─> OpenAI 兼容处理 ─> 请求预处理
Web 管理台 ── /api/admin/playground/openai/v1 + Cookie ┘                       │
                                                                              v
CodeBuddy 上游 <─ 流式请求与响应转换 <─ 用户凭证选择与轮换
```

主要职责边界：

- `web.py`：FastAPI 组装、路由挂载和 Uvicorn 本地入口。
- `config.py`：启动配置、用户级设置及其持久化。
- `src/auth_*.py`、`src/*_store.py`：系统用户、会话和 API Key。
- `src/openai_router.py`、`src/openai_compat.py`：OpenAI 协议入口和响应兼容。
- `src/codebuddy_*.py`、`src/credential_*.py`：CodeBuddy OAuth、凭证存储与轮换。
- `src/stream_service.py`、`src/sse.py`：上游流式请求、SSE 解析和非流式聚合。
- `frontend/`：Vue 管理台和旧版单文件管理台。

## 开发与验证

安装包含测试工具的开发依赖：

```bash
source venv/bin/activate
python3 -m pip install -r requirements-dev.txt
```

后端使用标准库 `unittest` 和 `coverage.py`，对 `config.py`、`web.py`、`src/` 强制执行行/分支综合 100% 覆盖率：

```bash
python3 -m coverage run -m unittest discover -s tests
python3 -m coverage report
```

前端验证：

```bash
cd frontend
pnpm run test:coverage
pnpm run typecheck
pnpm run lint
pnpm run build
```

## 故障排除

### `No authentication users configured. Mount secrets/users.txt.`

确认 `CODEBUDDY_USERS_FILE` 指向可读的用户文件，并且文件中至少有一条有效的 `用户名:PBKDF2哈希` 记录。

### `Invalid authentication credentials`

- 外部客户端必须请求 `/openai/v1/*` 并发送 `Authorization: Bearer sk-...`。
- 管理台测试请求必须访问 `/api/admin/playground/openai/v1/*` 并携带有效会话 Cookie。
- API Key 所属系统用户从 `users.txt` 删除后，该 Key 也会失效。

### `凭证获取失败` 或没有可用模型

当前系统用户没有可用的 CodeBuddy 上游凭证。登录管理台重新认证、添加凭证，并使用凭证测试功能确认状态。

### `CodeBuddy API error: 401` 或 `403`

这是上游 CodeBuddy 拒绝凭证，不是本系统 API Key 失效。重新完成 CodeBuddy 认证或替换上游凭证。

### `Invalid host header`

把实际访问域名加入 `CODEBUDDY_ALLOWED_HOSTS` 后重启服务。

### 查看详细日志

在 `.env` 中设置 `CODEBUDDY_LOG_LEVEL=DEBUG`，然后重启服务。日志可能包含请求元数据，不要在公开场合直接粘贴完整日志。

## 授权协议

本仓库当前的源代码基于 MIT 许可证授权。

本仓库是无任何开源协议授权的上游项目 [xueyue33/codebuddy2api](https://github.com/xueyue33/codebuddy2api) 的一个 fork，并保留了原始 Git 提交历史，以用于署名和透明性说明。MIT 许可证仅适用于该 fork 维护者在当前工作区中独立重写的代码。该许可证不适用于历史提交、原上游项目代码，或任何可能出现在 Git 历史中的第三方材料。具体信息可参考 [LICENSING.md](LICENSING.md)。
