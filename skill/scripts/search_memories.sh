#!/bin/bash
# SinoVec 记忆搜索脚本
# 用法: ./search_memories.sh "查询关键词" [topK]

QUERY="${1:-}"
TOPK="${2:-3}"

if [ -z "$QUERY" ]; then
    echo "用法: $0 \"查询内容\" [topK]"
    exit 1
fi

# 从环境变量文件读取配置（由 install.sh 生成）
if [ -f /etc/default/sinovec ]; then
    source /etc/default/sinovec
fi

API_KEY="${MEMORY_API_KEY:-}"
API_URL="http://${MEMORY_API_HOST:-127.0.0.1}:${MEMORY_API_PORT:-18793}"

if [ -z "$API_KEY" ]; then
    echo '{"error": "MEMORY_API_KEY 未设置，请检查 /etc/default/sinovec"}'
    exit 1
fi

ENCODED_QUERY=$(python3 -c "import urllib.parse; import sys; print(urllib.parse.quote(sys.argv[1]))" "$QUERY")
curl -s -X GET "${API_URL}/search?q=${ENCODED_QUERY}&top_k=${TOPK}&api_key=${API_KEY}" \
  -H "Content-Type: application/json" 2>/dev/null || echo '{"error": "服务不可用或查询失败"}'
