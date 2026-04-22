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

# 递归向上搜索找 memory_sinovec.py 或 config.env（无层级限制）
_find_sinovec_root() {
    local dir="$1"
    while [ -n "$dir" ] && [ "$dir" != "/" ]; do
        if [ -f "$dir/memory_sinovec.py" ] || [ -f "$dir/config.env" ]; then
            echo "$dir"
            return 0
        fi
        dir="$(dirname "$dir")"
    done
    return 1
}
SINOVEC_HOME="$(_find_sinovec_root "$SCRIPT_DIR")"

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

# 使用 python3 做可靠 URL 编码（正确处理多词查询和特殊字符）
# 修复：原实现 $QUERY 未加引号，多词查询会被 split 切割
ENCODED_QUERY=$(python3 -c "
import urllib.parse, sys
query = ' '.join(sys.argv[1:])
print(urllib.parse.quote(query, safe=''))
" "$QUERY")
curl -s --max-time 10 -X GET "${API_URL}/search?q=${ENCODED_QUERY}&top_k=${TOPK}" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${API_KEY}" 2>/dev/null || echo '{"error": "服务不可用或查询失败"}'
