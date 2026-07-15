#!/bin/sh
set -e

# 此脚本以 root 启动，完成挂载目录准备后切换到应用用户运行服务。

# 应用运行用户，需要与 Dockerfile 中创建的用户一致。
APP_USER="appuser"

case "${1:-}" in
    hash-password)
        shift
        exec codebuddy2api-hash-password "$@"
        ;;
    add-user)
        shift
        users_file="/app/secrets/users.txt"
        users_dir="$(dirname "${users_file}")"

        mkdir -p "${users_dir}"
        codebuddy2api-hash-password "$@" --output "${users_file}"
        echo "User entry written to ${users_file}." >&2
        exit 0
        ;;
    codebuddy2api-hash-password)
        exec "$@"
        ;;
esac

# 容器数据库固定写入持久化挂载点，禁止环境文件将其重定向到镜像层。
CODEBUDDY_DATA_DIR="/app/data"
export CODEBUDDY_DATA_DIR

# 宿主用户文件保持严格权限，由 root 复制为应用用户只读的运行时副本。
users_file="/app/secrets/users.txt"
runtime_users_dir="/run/codebuddy2api"
runtime_users_file="/run/codebuddy2api/users.txt"
if [ -L "${users_file}" ] || [ ! -f "${users_file}" ]; then
    echo "Authentication users file must be a regular non-symbolic-link file: ${users_file}" >&2
    exit 1
fi
install -d -m 700 -o "${APP_USER}" -g "${APP_USER}" "${runtime_users_dir}"
install -m 400 -o "${APP_USER}" -g "${APP_USER}" \
    "${users_file}" "${runtime_users_file}"
CODEBUDDY_USERS_FILE="${runtime_users_file}"
export CODEBUDDY_USERS_FILE

# 确保挂载目录中的普通目录和普通文件归应用用户所有。
# 使用 find 默认不跟随符号链接，避免递归 chown 误处理链接目标。
echo "Ensuring ownership of mounted directories..."
find /app/data -type d -exec chown "${APP_USER}:${APP_USER}" {} +
find /app/data -type f -exec chown "${APP_USER}:${APP_USER}" {} +
echo "Ownership fixed."

# 容器 Uvicorn 与应用使用同一日志级别，并关闭默认 Server 响应头。
if [ "${1:-}" = "uvicorn" ]; then
    log_level="$(printf '%s' "${CODEBUDDY_LOG_LEVEL:-INFO}" | tr '[:upper:]' '[:lower:]')"
    set -- "$@" --log-level "${log_level}" --no-server-header
    max_concurrent_requests="${CODEBUDDY_MAX_CONCURRENT_REQUESTS:-}"
    if [ -n "${max_concurrent_requests}" ]; then
        # 复用应用的 Uvicorn 0.49.0 边界补偿，确保配置 N 实际放行 N 个连接。
        uvicorn_limit_concurrency="$(python3 -c \
            'import sys; from src.uvicorn_limits import to_uvicorn_limit_concurrency; print(to_uvicorn_limit_concurrency(int(sys.argv[1])))' \
            "${max_concurrent_requests}")"
        set -- "$@" --limit-concurrency "${uvicorn_limit_concurrency}"
    fi
fi

# 切换到应用用户并执行 Dockerfile 中的 CMD。
# exec 会替换当前进程，确保应用能正确接收停止信号。
echo "Executing command as user ${APP_USER}: $@"
exec gosu "${APP_USER}" "$@"
