#!/bin/bash
# SinoVec 记忆搜索脚本
# 用法: ./search_memories.sh "查询关键词" [topK]

QUERY="${1:-}"
TOPK="${2:-3}"
API_KEY="sinovec_secret_key_2024"
API_URL="http://127.0.0.1:18793"

if [ -z "$QUERY" ]; then
    echo "用法: $0 \"查询内容\" [topK]"
    exit 1
fi

ENCODED_QUERY=$(python3 -c "import urllib.parse; import sys; print(urllib.parse.quote(sys.argv[1]))" "$QUERY")
curl -s -X GET "${API_URL}/search?q=${ENCODED_QUERY}&top_k=${TOPK}&api_key=${API_KEY}" \
  -H "Content-Type: application/json" 2>/dev/null || echo '{"error": "服务不可用或查询失败"}'
