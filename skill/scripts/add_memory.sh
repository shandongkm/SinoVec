#!/bin/bash
# SinoVec 添加记忆脚本（通过 CLI）
# 用法: ./add_memory.sh "记忆内容" [user_id]

DATA="${1:-}"
USER_ID="${2:-主人}"

if [ -z "$DATA" ]; then
    echo "用法: $0 \"记忆内容\" [user_id]"
    exit 1
fi

# 从环境变量文件读取配置（由 install.sh 生成）
if [ -f /etc/default/sinovec ]; then
    source /etc/default/sinovec
fi

cd /opt/SinoVec
MEMORY_DB_HOST="${MEMORY_DB_HOST:-127.0.0.1}" \
MEMORY_DB_PORT="${MEMORY_DB_PORT:-5432}" \
MEMORY_DB_NAME="${MEMORY_DB_NAME:-sinovec}" \
MEMORY_DB_USER="${MEMORY_DB_USER:-sinovec}" \
MEMORY_DB_PASS="${MEMORY_DB_PASS:-}" \
MEMORY_API_KEY="${MEMORY_API_KEY:-}" \
python3 memory_sinovec.py add "$DATA" --user "$USER_ID" 2>&1
