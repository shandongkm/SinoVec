#!/bin/bash
# SinoVec 添加记忆脚本（通过 HTTP API）
# 用法: ./add_memory.sh "记忆内容" [user_id]
#
# 优先使用 HTTP API（只需 API Key，不需 DB 密码）。
# 回退到 CLI 模式（当 HTTP 不可用时自动切换）。

DATA="${1:-}"
USER_ID="${2:-主人}"

if [ -z "$DATA" ]; then
    echo "用法: $0 \"记忆内容\" [user_id]"
    exit 1
fi

# ── 确定项目根目录（兼容标准安装和 OpenClaw 技能目录结构）─────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 多层向上搜索，找有 memory_sinovec.py 的目录
SINOVEC_HOME=""
for depth in 2 3 4 5 6; do
    candidate="$SCRIPT_DIR"
    for ((i=1; i<=depth; i++)); do
        candidate="$(dirname "$candidate")"
    done
    if [ -f "$candidate/memory_sinovec.py" ]; then
        SINOVEC_HOME="$candidate"
        break
    fi
done

# 备选路径（多层搜索均未命中时）
if [ -z "$SINOVEC_HOME" ] && [ -f "/opt/SinoVec/memory_sinovec.py" ]; then
    SINOVEC_HOME="/opt/SinoVec"
fi

# ── 加载配置 ──────────────────────────────────────────────────────
# 优先从普通用户可读的 config.env 读 API Key
if [ -n "$SINOVEC_HOME" ] && [ -f "$SINOVEC_HOME/config.env" ]; then
    set -a
    source "$SINOVEC_HOME/config.env" 2>/dev/null
    set +a
fi

# fallback：直接读 /etc/default/sinovec（需要 root 权限，读不到则继续）
if [ -z "$MEMORY_API_KEY" ]; then
    if [ -r /etc/default/sinovec ]; then
        set -a
        source /etc/default/sinovec 2>/dev/null
        set +a
    fi
fi

# ── API 端点和凭证 ────────────────────────────────────────────────
API_KEY="${MEMORY_API_KEY:-}"
API_URL="http://127.0.0.1:${MEMORY_API_PORT:-18793}"

if [ -z "$API_KEY" ]; then
    echo "错误: MEMORY_API_KEY 未设置。请确保 SinoVec 已正确安装。" >&2
    exit 1
fi

# ── 构造 JSON payload（对特殊字符做转义）──────────────────────────
# 使用 python3 做可靠的 JSON 转义
JSON_PAYLOAD=$(python3 -c "
import json, sys
obj = {'text': sys.argv[1], 'user_id': sys.argv[2]}
print(json.dumps(obj, ensure_ascii=False))
" "$DATA" "$USER_ID")

# ── 发送 HTTP POST 请求 ──────────────────────────────────────────
HTTP_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
    "${API_URL}/memory" \
    -H "Content-Type: application/json; charset=utf-8" \
    -H "X-API-Key: ${API_KEY}" \
    -d "$JSON_PAYLOAD" 2>&1)
HTTP_CODE=$(echo "$HTTP_RESPONSE" | tail -1)
BODY=$(echo "$HTTP_RESPONSE" | sed '$d')

# ── 解析响应 ─────────────────────────────────────────────────────
if [ "$HTTP_CODE" = "201" ]; then
    echo "✅ 添加成功"
    echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  ID: {d.get(\"id\",\"\")}')" 2>/dev/null || true
elif [ "$HTTP_CODE" = "409" ]; then
    echo "⚠️  添加失败（内容重复或质量门拒绝）"
    echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  {d.get(\"error\",\"\")}')" 2>/dev/null || echo "  $BODY"
    exit 1
elif [ "$HTTP_CODE" = "401" ]; then
    echo "❌ 认证失败：API Key 无效或服务未启动" >&2
    exit 1
elif [ "$HTTP_CODE" = "000" ]; then
    # curl 无法连接，回退到 CLI 模式
    echo "⚠️  HTTP 服务不可用，尝试 CLI 模式..."
    if [ -z "$SINOVEC_HOME" ]; then
        echo "❌ 无法找到 SinoVec 安装目录，CLI 模式失败" >&2
        exit 1
    fi
    # CLI 模式需要 DB 密码，按优先级尝试多个配置位置
    _CLI_CFG_LOADED=false
    # 1. skill-credentials.env（OpenClaw 技能安装时生成）
    if [ -r "$SCRIPT_DIR/../skill-credentials.env" ]; then
        set -a
        source "$SCRIPT_DIR/../skill-credentials.env" 2>/dev/null && _CLI_CFG_LOADED=true
        set +a
    fi
    # 2. /etc/default/sinovec（标准 root 安装）
    if [ "$_CLI_CFG_LOADED" = "false" ] && [ -r /etc/default/sinovec ]; then
        set -a
        source /etc/default/sinovec 2>/dev/null && _CLI_CFG_LOADED=true
        set +a
    fi
    if [ "$_CLI_CFG_LOADED" = "false" ]; then
        echo "❌ CLI 模式无法找到数据库配置（需要 MEMORY_DB_PASS）" >&2
        exit 1
    fi
    export MEMORY_DB_PASS
    cd "$SINOVEC_HOME"
    python3 memory_sinovec.py add "$DATA" --user "$USER_ID" 2>&1
else
    echo "❌ HTTP 错误 ($HTTP_CODE): $BODY" >&2
    exit 1
fi
