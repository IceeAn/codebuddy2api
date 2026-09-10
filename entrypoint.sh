#!/bin/sh
set -e

# 此脚本以 root 启动，准备持久化目录和可选的旧账号迁移文件后降权。
APP_USER="appuser"

# 退役命令只输出迁移指引，不应读取或写入任何挂载文件。
case "${1:-}" in
    hash-password|codebuddy2api-hash-password)
        shift
        exec gosu "${APP_USER}" codebuddy2api-hash-password "$@"
        ;;
esac

# 容器数据库固定写入持久化挂载点，禁止环境文件将其重定向到镜像层。
CODEBUDDY_DATA_DIR="/app/data"
export CODEBUDDY_DATA_DIR
mkdir -p /app/data

# find 默认不跟随符号链接；数据库层还会独立拒绝不安全路径。
echo "Ensuring ownership of mounted data..."
find /app/data -type d -exec chown "${APP_USER}:${APP_USER}" {} +
find /app/data -type f -exec chown "${APP_USER}:${APP_USER}" {} +

# users.txt 仅作为首次迁移源。缺失是合法的全新安装状态。
users_file="/app/secrets/users.txt"
runtime_users_dir="/run/codebuddy2api"
runtime_users_file="/run/codebuddy2api/users.txt"
install -d -m 700 -o "${APP_USER}" -g "${APP_USER}" "${runtime_users_dir}"
rm -f "${runtime_users_file}"
if [ -e "${users_file}" ] || [ -L "${users_file}" ]; then
    if [ -L "${users_file}" ] || [ ! -f "${users_file}" ]; then
        echo "Legacy users file must be a regular non-symbolic-link file: ${users_file}" >&2
        exit 1
    fi
    hard_link_count="$(stat -c %h "${users_file}")"
    if [ "${hard_link_count}" -ne 1 ]; then
        echo "Legacy users file must not have multiple hard links: ${users_file}" >&2
        exit 1
    fi
    if find "${users_file}" -perm /077 -print -quit | grep -q .; then
        echo "WARNING: legacy users file permissions are broader than 0600: ${users_file}" >&2
    fi
    install -m 400 -o "${APP_USER}" -g "${APP_USER}" "${users_file}" "${runtime_users_file}"
fi
CODEBUDDY_USERS_FILE="${runtime_users_file}"
export CODEBUDDY_USERS_FILE

case "${1:-}" in
    set-user|add-user|list-users|delete-user)
        command_name="$1"
        shift
        exec gosu "${APP_USER}" codebuddy2api-manage-users "${command_name}" "$@"
        ;;
esac

# 容器 Uvicorn 与应用使用同一日志级别，并关闭默认 Server 响应头。
if [ "${1:-}" = "uvicorn" ]; then
    log_level="$(printf '%s' "${CODEBUDDY_LOG_LEVEL:-INFO}" | tr '[:upper:]' '[:lower:]')"
    set -- "$@" --log-level "${log_level}" --no-server-header
    max_concurrent_requests="${CODEBUDDY_MAX_CONCURRENT_REQUESTS:-}"
    if [ -n "${max_concurrent_requests}" ]; then
        uvicorn_limit_concurrency="$(python3 -c 'import sys; from src.uvicorn_limits import to_uvicorn_limit_concurrency; print(to_uvicorn_limit_concurrency(int(sys.argv[1])))' "${max_concurrent_requests}")"
        set -- "$@" --limit-concurrency "${uvicorn_limit_concurrency}"
    fi
fi

echo "Executing command as user ${APP_USER}: $@"
exec gosu "${APP_USER}" "$@"
