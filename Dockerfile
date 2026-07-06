# 使用满足 Babel 8 / Vite 8 最低版本要求的 Node 构建 Vue 管理台
FROM node:24.11.1-slim AS frontend-build

WORKDIR /frontend

COPY frontend/package.json ./
COPY frontend/pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm run build

# 使用官方的、轻量级的 Python 镜像作为运行时基础
FROM python:3.11-slim

# 设置容器内的工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 安装依赖
# --no-cache-dir 选项可以减小镜像体积
RUN python3 -m pip install --no-cache-dir -r requirements.txt

# 将项目的所有文件复制到工作目录中
COPY . .

# 复制前端构建产物；源码仓库不提交 frontend/dist
COPY --from=frontend-build /frontend/dist /app/frontend/dist

# 安装 gosu，一个轻量级的 su/sudo 替代品，用于在脚本中切换用户
# 并在同一层中进行清理以减小镜像体积
RUN apt-get update && \
    apt-get install -y gosu && \
    rm -rf /var/lib/apt/lists/*

# 复制并设置入口脚本
COPY entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["entrypoint.sh"]

# 创建一个非root用户来运行应用
RUN useradd -m -u 1001 appuser

# 创建运行时挂载目录。secrets 目录只用于挂载只读用户文件。
RUN mkdir -p /app/data /app/.codebuddy_creds /app/secrets && \
    chown -R appuser:appuser /app/data /app/.codebuddy_creds

# 声明容器将要监听的端口
# 这个端口应该与您在配置中设置的 CODEBUDDY_PORT 一致
EXPOSE 8001

# 定义容器启动时要执行的命令
# 使用 Uvicorn 启动 ASGI 应用，并保持与本地启动一致地关闭访问日志
CMD ["uvicorn", "web:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log"]
