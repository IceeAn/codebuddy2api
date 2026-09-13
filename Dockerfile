# syntax=docker/dockerfile:1

ARG NODE_VERSION=24.15.0
ARG PYTHON_VERSION=3.12

# 使用满足前端工具链最低版本要求的 Node 构建 Vue 管理台。
FROM --platform=$BUILDPLATFORM node:${NODE_VERSION}-slim AS frontend-build

WORKDIR /frontend

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm run build

# 分词数据在构建阶段锁定下载，运行时完全离线。
FROM --platform=$BUILDPLATFORM python:${PYTHON_VERSION}-slim AS tokenizer-assets
WORKDIR /download
COPY src/tokenizer_catalog.json ./src/tokenizer_catalog.json
COPY scripts/download_tokenizers.py ./scripts/download_tokenizers.py
RUN python3 scripts/download_tokenizers.py

# ARMv7 缺少部分原生 wheel；仅构建阶段安装 Rust/C 编译器。
FROM python:${PYTHON_VERSION}-slim AS python-wheels
ARG RUST_VERSION=1.94.0
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o /tmp/rustup.sh && \
    sh /tmp/rustup.sh -y --profile minimal --default-toolchain "${RUST_VERSION}" && rm /tmp/rustup.sh
ENV PATH="/root/.cargo/bin:${PATH}"
COPY requirements.txt /requirements.txt
RUN python3 -m pip wheel --no-cache-dir --wheel-dir /wheels -r /requirements.txt

# 运行时使用与 CI 推荐版本一致的 Python。
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 容器内无外部时区配置时使用上海时区，签到等本地自然日调度依赖它。
ENV TZ=Asia/Shanghai

WORKDIR /app

# gosu 用于入口脚本完成挂载目录准备后降权运行服务。
RUN apt-get update && \
    apt-get install -y --no-install-recommends gosu tzdata && \
    ln -fs /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
COPY --from=python-wheels /wheels /wheels
RUN python3 -m pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels

COPY LICENSE LICENSING.md ./
COPY config.py release_runtime_lock.py web.py ./
COPY src ./src
COPY --from=tokenizer-assets /download/src/tokenizer_assets ./src/tokenizer_assets
COPY scripts/hash_password.py ./scripts/hash_password.py
COPY scripts/manage_users.py ./scripts/manage_users.py
COPY frontend/public ./frontend/public
COPY --from=frontend-build /frontend/dist /app/frontend/dist
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

# 创建运行用户、持久化目录和镜像内辅助命令。
RUN useradd --create-home --uid 1001 appuser && \
    mkdir -p /app/data /app/secrets && \
    chown -R appuser:appuser /app/data && \
    chmod +x /usr/local/bin/entrypoint.sh /app/scripts/hash_password.py /app/scripts/manage_users.py && \
    ln -s /app/scripts/hash_password.py /usr/local/bin/codebuddy2api-hash-password && \
    ln -s /app/scripts/manage_users.py /usr/local/bin/codebuddy2api-manage-users

EXPOSE 8001

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "web:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log"]
