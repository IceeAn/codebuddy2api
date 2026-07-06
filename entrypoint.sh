#!/bin/sh
set -e

# 此脚本以 root 启动，完成挂载目录准备后切换到应用用户运行服务。

# 应用运行用户，需要与 Dockerfile 中创建的用户一致。
APP_USER="appuser"

# 容器数据库固定写入持久化挂载点，禁止环境文件将其重定向到镜像层。
CODEBUDDY_DATA_DIR="/app/data"
export CODEBUDDY_DATA_DIR

# 确保挂载目录中的普通目录和普通文件归应用用户所有。
# 使用 find 默认不跟随符号链接，避免递归 chown 误处理链接目标。
echo "Ensuring ownership of mounted directories..."
find /app/data /app/.codebuddy_creds -type d -exec chown ${APP_USER}:${APP_USER} {} +
find /app/data /app/.codebuddy_creds -type f -exec chown ${APP_USER}:${APP_USER} {} +
echo "Ownership fixed."

# 切换到应用用户并执行 Dockerfile 中的 CMD。
# exec 会替换当前进程，确保应用能正确接收停止信号。
echo "Executing command as user ${APP_USER}: $@"
exec gosu ${APP_USER} "$@"
