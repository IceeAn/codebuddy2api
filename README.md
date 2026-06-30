# CodeBuddy2API

将 CodeBuddy 官方 API 包装成一个功能强大、与 OpenAI API 格式兼容的服务。本项目可以直接调用 CodeBuddy 官方
API，并为所有标准客户端提供统一的接口。

## 🌟 功能特性

- 🔌 **OpenAI 兼容接口**：支持标准的 `/v1/chat/completions` API，无缝对接现有生态。
- 🔄 **智能响应处理**：即使 CodeBuddy 原生仅支持流式响应，本服务也能为客户端智能处理**非流式**请求，并在后端自动完成“流式转非流式”的响应包装。
- ⚡ **高性能**：完全基于 FastAPI 和 `asyncio` 构建，支持高并发异步请求。
- 🔐 **双重认证机制**：
    - **服务访问认证**：支持 `secrets/users.txt` 多用户密码哈希认证，适合团队部署。
    - **CodeBuddy 官方认证**：每个本系统用户在后端分别管理自己的 CodeBuddy `Bearer Token`。
- 🔄 **凭证自动轮换**：每个本系统用户都可以维护多个 CodeBuddy 认证凭证，服务会在该用户自己的凭证池内自动轮换。
- 🌐 **Web 管理界面**：内置控制台 Web UI，方便用户管理凭证、测试 API 和查看服务状态。

## 🚀 快速开始

### 1. 前置要求

- Python 3.10 或更高版本
- Node.js 24 LTS 或更高版本（可选，用于构建 Web 管理台）
- Git

### 2. 下载和安装

首先，克隆本项目到本地：

```bash
git clone https://github.com/iceean/codebuddy2api.git
cd codebuddy2api
```

然后，创建 Python 虚拟环境并安装后端依赖：

```bash
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -r requirements.txt
```

可选：构建新版 Web 管理台。未构建时，服务启动后 `/` 会回退到旧版单文件管理台。

```bash
cd frontend
pnpm install
pnpm run build
cd ..
```

启动服务：
```bash
python3 web.py
```

### 3. 配置环境变量

项目启动需要一些基本配置。请将根目录下的 `.env.example` 文件复制一份并重命名为 `.env`：

```bash
cp .env.example .env
```

然后，生成多用户认证文件。每行一个用户，格式为 `用户名:密码哈希`：

```bash
mkdir -p secrets
python3 scripts/hash_password.py admin >> secrets/users.txt
chmod 600 secrets/users.txt
```

公网部署时，还需要在 `.env` 中设置访问域名白名单：

```dotenv
CODEBUDDY_ALLOWED_HOSTS=api.example.com,127.0.0.1,localhost
CODEBUDDY_ALLOWED_ORIGINS=https://api.example.com
```

### 4. 添加 CodeBuddy 认证凭证

为了让服务能够代理请求，每个本系统用户至少需要添加一个自己的 CodeBuddy 认证凭证。本项目提供了极为便捷的**自动化认证**方式。

**推荐方式：使用 Web 管理界面自动获取**

1. 启动服务后，使用浏览器访问 `http://127.0.0.1:8001` (或你自定义的地址)。
2. 输入 `secrets/users.txt` 中配置的用户名和密码登录管理面板。
3. 进入 “**凭证管理**” 标签页。
4. 点击 **自动获取认证** 卡片中的 “**开始认证**” 按钮。
5. 系统会自动生成一个 CodeBuddy 的官方登录链接。请点击 “**打开链接**” 按钮。
6. 在新打开的 CodeBuddy 页面中完成登录授权。
7. **完成！**
   登录成功后，请关闭登录页面。本服务会自动检测到登录状态，并为当前登录用户获取、解析和保存新的认证凭证。你只需点击 “**刷新列表
   **” 即可看到新添加的凭证。

### 5. 启动服务

一切准备就绪后，直接运行：

```bash
# 确保你已在虚拟环境中 (source venv/bin/activate)
python3 web.py
```

服务启动后，你就可以开始使用了！

### Docker 部署

```bash
cp .env.example .env
mkdir -p secrets config .codebuddy_creds
python3 scripts/hash_password.py admin >> secrets/users.txt
chmod 644 secrets/users.txt
```

编辑 `.env`，至少设置公网访问域名：

```dotenv
CODEBUDDY_ALLOWED_HOSTS=api.example.com,127.0.0.1,localhost
CODEBUDDY_ALLOWED_ORIGINS=https://api.example.com
CODEBUDDY_SSL_VERIFY=true
```

启动：

```bash
docker compose up -d --build
```

Docker 镜像使用多阶段构建：Node 阶段生成 `frontend/dist/`，Python 阶段只运行 FastAPI/Hypercorn。源码仓库不提交
`frontend/dist/`；本地不用 Docker 启动时，新版前端构建是可选的。服务启动后，`/` 优先返回新版 Vue 管理台；
若 `frontend/dist/index.html` 不存在，`/` 回退到 `frontend/admin.html` 旧版单文件管理台。`/admin` 始终返回旧版管理台，
两者共用 `/api/admin/*` 管理 API。

`docker-compose.yml` 会把 `./secrets/users.txt` 以只读方式挂载到容器内；容器内应用用户需要可读该文件。Linux
主机如果想使用更严格权限，可以把文件所有者改为容器内 UID 1001 后再设置 `chmod 400 secrets/users.txt`。不要把真实
`secrets/users.txt`、`config/` 或 `.codebuddy_creds/` 提交到代码仓库。

## ⚙️ API 使用

### 认证

所有对本服务的 API 请求都需要认证。Web 管理页使用登录接口签发的 `HttpOnly` 会话 Cookie，刷新页面后会自动恢复登录态；外部客户端必须在
Web 管理页的「API 密钥」中生成 `sk-...` API Key，并通过 Bearer 方式传入。本服务不再接受 Basic Auth 或 Bearer `用户名:密码`
作为 API 认证。

```http
Authorization: Bearer sk-your_api_key
```

### 客户端集成示例

你可以将任何支持 OpenAI API 的客户端指向本服务。

**Python 客户端:**

```python
import openai

client = openai.OpenAI(
    api_key="sk-your_api_key",
    base_url="http://127.0.0.1:8001/codebuddy/v1"
)

# 非流式请求
response = client.chat.completions.create(
    model="glm-5.1",
    messages=[
        {"role": "user", "content": "你好，2+2等于几？"}
    ]
)
print(response.choices[0].message.content)

# 流式请求
stream = client.chat.completions.create(
    model="glm-5.1",
    messages=[
        {"role": "user", "content": "写一个Python的Hello World脚本"}
    ],
    stream=True
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")

```

**curl 命令行示例:**

```bash
# 非流式请求
curl -X POST "http://127.0.0.1:8001/codebuddy/v1/chat/completions" \
  -H "Authorization: Bearer sk-your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "glm-5.1",
    "messages": [
      {"role": "user", "content": "Hello, what is 2+2?"}
    ]
  }'

# 流式请求
curl -X POST "http://127.0.0.1:8001/codebuddy/v1/chat/completions" \
  -H "Authorization: Bearer sk-your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "glm-5.1",
    "messages": [
      {"role": "user", "content": "Write a Python hello world script"}
    ],
    "stream": true
  }'
```

## 📝 API 端点

- `POST /codebuddy/v1/chat/completions`: 核心接口，用于发送聊天请求。
- `GET /codebuddy/v1/models`: 获取当前用户可用模型列表，包含用户设置的附加模型与 CodeBuddy 真实模型。
- `POST /auth/login`: Web 管理台登录并签发 HttpOnly 会话 Cookie。
- `GET /auth/session`: 恢复 Web 管理台会话状态。
- `POST /auth/logout`: 清理 Web 管理台会话。
- `GET /api/admin/status`: 管理台总览状态、凭证统计和使用统计。
- `GET/POST /api/admin/api-keys`: 管理当前用户创建的 `sk-...` API Key。
- `GET/POST /api/admin/credentials`: 管理当前用户的 CodeBuddy 凭证。
- `POST /api/admin/credentials/{credential_id}/test`: 使用指定凭证发起精确测试。
- `GET/PUT /api/admin/settings`: 读取和保存当前登录用户的管理台设置；未保存时使用系统默认配置。
- `GET /health`: 服务的健康检查端点。

## 🔧 项目结构

```
codebuddy2api/
├── src/                           # 源代码目录
│   ├── auth_router.py             # 管理页登录与 API Key 路由
│   ├── auth_types.py              # 认证类型与常量
│   ├── users_store.py             # users.txt 用户密码存储
│   ├── api_key_store.py           # sk- API Key 存储
│   ├── session_store.py           # 管理页会话存储
│   ├── admin_router.py            # 管理台专用 API 路由
│   ├── codebuddy_api_client.py    # CodeBuddy 请求头生成
│   ├── codebuddy_auth_router.py   # CodeBuddy OAuth2 认证路由
│   ├── codebuddy_oauth.py         # CodeBuddy OAuth 上游客户端与 token 保存
│   ├── codebuddy_token_manager.py # CodeBuddy 凭证管理门面
│   ├── credential_store.py        # CodeBuddy 凭证文件安全读写
│   ├── credential_rotation.py     # CodeBuddy 凭证过期判断与轮换策略
│   ├── codebuddy_router.py        # 核心 API 路由 (v1)
│   ├── request_processor.py       # 聊天请求验证与载荷预处理
│   ├── stream_service.py          # 上游流式请求与非流式聚合
│   ├── sse.py                     # SSE 解析与格式化
│   ├── openai_compat.py           # OpenAI 兼容格式转换
│   ├── frontend_router.py         # 新旧管理台静态资源路由
│   ├── usage_stats_manager.py     # 使用统计管理器
│   └── keyword_replacer.py        # 关键词替换模块
├── frontend/
│   ├── src/                       # Vue 3 + TypeScript 管理台源码
│   ├── admin.html                 # 旧版单文件管理台（服务于 /admin）
│   ├── package.json               # 前端依赖与脚本
│   └── dist/                      # 本地构建产物（Git 忽略）
├── scripts/
│   └── hash_password.py           # 生成 users.txt 密码哈希
├── secrets/
│   └── users.txt.example          # 多用户认证文件示例
├── .codebuddy_creds/              # 按本系统用户隔离存放CodeBuddy凭证的目录 (Git会忽略其中的文件)
├── web.py                         # FastAPI服务主入口
├── config.py                      # 环境变量配置管理
├── requirements.txt               # Python依赖列表
├── .env.example                   # 环境变量示例文件
├── docker-compose.yml             # Docker Compose 配置
├── Dockerfile                     # Docker 镜像构建文件
├── entrypoint.sh                  # Docker 容器入口脚本
└── README.md                      # 本文档
```

## ⚙️ 配置选项

所有配置均通过 `.env` 文件或环境变量进行管理。

| 环境变量                                | 默认值                                                    | 说明                                                           |
|-------------------------------------|--------------------------------------------------------|--------------------------------------------------------------|
| `CODEBUDDY_USERS_FILE`              | `secrets/users.txt`                                    | 多用户认证文件路径。                                                   |
| `CODEBUDDY_HOST`                    | `127.0.0.1`                                            | 服务监听的主机地址。                                                   |
| `CODEBUDDY_PORT`                    | `8001`                                                 | 服务监听的端口。                                                     |
| `CODEBUDDY_API_ENDPOINT`            | `https://copilot.tencent.com`                          | CodeBuddy 官方 API 端点，默认中国站；国际站可改为 `https://www.codebuddy.ai`。 |
| `CODEBUDDY_ALLOWED_API_ENDPOINTS`   | `https://copilot.tencent.com,https://www.codebuddy.ai` | 允许转发真实 CodeBuddy Token 的上游端点白名单。                             |
| `CODEBUDDY_CREDS_DIR`               | `.codebuddy_creds`                                     | 存放 CodeBuddy 认证凭证的根目录；实际凭证按本系统用户隔离到 `users/<用户目录>/`。         |
| `CODEBUDDY_ALLOWED_HOSTS`           | `localhost,127.0.0.1`                                  | 允许访问本服务的 Host 头，公网部署必须加入实际域名。                                |
| `CODEBUDDY_ALLOWED_ORIGINS`         | 空                                                      | 允许跨域调用的前端 Origin；留空表示不启用跨域。                                  |
| `CODEBUDDY_LOG_LEVEL`               | `INFO`                                                 | 启动时日志级别，可选 `DEBUG`, `INFO`, `WARNING`, `ERROR`；不支持运行时修改。        |
| `CODEBUDDY_MODELS`                  | `glm-5.2,deepseek-v4-pro`                              | 用户设置默认值：附加模型列表，用逗号分隔；系统会通过 CodeBuddy 动态获取全部可用模型，与此列表做并集。 |
| `CODEBUDDY_FORCED_REASONING_MODELS` | `deepseek-v4-pro,deepseek-v4-flash,glm-5.1,glm-5.2`    | 用户设置默认值：强制注入推理参数的模型列表，用逗号分隔；留空则关闭强制推理。                    |
| `CODEBUDDY_FORCED_TEMPERATURE`      | `1`                                                    | 用户设置默认值：强制覆盖上游请求的 `temperature`；留空则不覆盖客户端传入值。             |
| `CODEBUDDY_STRIP_MODEL_NAMESPACE`   | `true`                                                 | 用户设置默认值：将 `provider/model` 形式的模型名在转发上游前改为 `model`；留空或 `false` 则不处理。 |
| `CODEBUDDY_AUTO_ROTATION_ENABLED`   | `true`                                                 | 用户设置默认值：是否启用凭证轮换。                                             |
| `CODEBUDDY_SSL_VERIFY`              | `true`                                                 | 上游 TLS 证书校验开关，公网部署必须保持 `true`。                               |
| `CODEBUDDY_ROTATION_COUNT`          | `1`                                                    | 用户设置默认值：凭证轮换频率，每 N 次请求后切换凭证；必须为正整数。                         |

## 🐛 故障排除

- **"No valid CodeBuddy credentials found"**:
    - 确保当前登录的本系统用户已经在 Web UI 中添加了至少一个有效的 CodeBuddy 凭证。
    - 推荐使用 Web UI 添加，以确保格式正确。

- **"API error: 401" / "API error: 403" (来自 CodeBuddy)**:
    - 这通常意味着你的 CodeBuddy `Bearer Token` 无效或已过期。请通过官网重新获取一个新的 Token，并在 Web UI 中更新。

- **"Invalid authentication credentials"**:
    - 检查客户端是否使用了 Web 管理页生成的 `sk-...` API Key，并以 `Authorization: Bearer sk-...` 传入。

- **需要查看详细日志**:
    - 在 `.env` 文件中设置 `CODEBUDDY_LOG_LEVEL=DEBUG`，然后重启服务。
