# syntax=docker/dockerfile:1

ARG NODE_VERSION=24.11.1
ARG PYTHON_VERSION=3.12

# 使用满足 Babel 8 / Vite 8 最低版本要求的 Node 构建 Vue 管理台。
FROM --platform=$BUILDPLATFORM node:${NODE_VERSION}-slim AS frontend-build

WORKDIR /frontend

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm run build

# 运行时使用与 CI 推荐版本一致的 Python。
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# gosu 用于入口脚本完成挂载目录准备后降权运行服务。
RUN apt-get update && \
    apt-get install -y --no-install-recommends gosu && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY LICENSE LICENSING.md ./
COPY config.py release_runtime_lock.py web.py ./
COPY src ./src
COPY scripts/hash_password.py ./scripts/hash_password.py
COPY frontend/public ./frontend/public
COPY --from=frontend-build /frontend/dist /app/frontend/dist
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

# 创建运行用户、持久化目录和镜像内辅助命令。
RUN useradd --create-home --uid 1001 appuser && \
    mkdir -p /app/data /app/secrets && \
    chown -R appuser:appuser /app/data && \
    chmod +x /usr/local/bin/entrypoint.sh /app/scripts/hash_password.py && \
    ln -s /app/scripts/hash_password.py /usr/local/bin/codebuddy2api-hash-password

EXPOSE 8001

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "web:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log"]
