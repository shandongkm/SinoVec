#!/bin/bash
# SinoVec 添加记忆脚本（通过 CLI）
# 用法: ./add_memory.sh "记忆内容" [user_id]

DATA="${1:-}"
USER_ID="${2:-主人}"

if [ -z "$DATA" ]; then
    echo "用法: $0 \"记忆内容\" [user_id]"
    exit 1
fi

cd /root/SinoVec
MEMORY_DB_HOST=127.0.0.1 \
MEMORY_DB_PORT=5432 \
MEMORY_DB_NAME=sinovec \
MEMORY_DB_USER=sinovec \
MEMORY_DB_PASS=sinovec_secure_pass \
MEMORY_API_KEY=sinovec_secret_key_2024 \
HF_HUB_PROXY=http://127.0.0.1:7890 \
python3 memory_sinovec.py add "$DATA" --user "$USER_ID" 2>&1
