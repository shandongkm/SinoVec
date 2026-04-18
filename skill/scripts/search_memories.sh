#!/bin/bash
# SinoVec 记忆搜索脚本
# 用法: ./search_memories.sh "查询关键词" [topK]

QUERY="${1:-}"
TOPK="${2:-3}"

if [ -z "$QUERY" ]; then
    echo "用法: $0 \"查询内容\" [topK]"
    exit 1
fi

# ── 确定项目根目录（兼容标准安装和 OpenClaw 技能目录结构）─────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 多层向上搜索，找有 config.env 或 memory_sinovec.py 的目录
SINOVEC_HOME=""
for depth in 2 3 4 5 6; do
    candidate="$SCRIPT_DIR"
    for ((i=1; i<=depth; i++)); do
        candidate="$(dirname "$candidate")"
    done
    if [ -f "$candidate/memory_sinovec.py" ] || [ -f "$candidate/config.env" ]; then
        SINOVEC_HOME="$candidate"
        break
    fi
done

# ── 加载配置（优先普通用户可读的 config.env，fallback 到 root 专属配置）──
CONFIG_LOADED=false
if [ -n "$SINOVEC_HOME" ] && [ -f "$SINOVEC_HOME/config.env" ]; then
    set -a
    source "$SINOVEC_HOME/config.env" 2>/dev/null && CONFIG_LOADED=true
    set +a
fi

if [ "$CONFIG_LOADED" = "false" ] && [ -r /etc/default/sinovec ]; then
    set -a
    source /etc/default/sinovec 2>/dev/null && CONFIG_LOADED=true
    set +a
fi

API_KEY="${MEMORY_API_KEY:-}"
API_URL="http://127.0.0.1:${MEMORY_API_PORT:-18793}"

if [ -z "$API_KEY" ]; then
    echo '{"error": "MEMORY_API_KEY 未设置，请先运行 install.sh 安装 SinoVec"}'
    exit 1
fi

ENCODED_QUERY=$(python3 -c "
import urllib.parse, sys
print(urllib.parse.quote(' '.join(sys.argv[1:])))
" $QUERY)
curl -s -X GET "${API_URL}/search?q=${ENCODED_QUERY}&top_k=${TOPK}&api_key=${API_KEY}" \
  -H "Content-Type: application/json" 2>/dev/null || echo '{"error": "服务不可用或查询失败"}'
