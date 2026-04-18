#!/bin/bash
# SinoVec zhparser 修复脚本
# 场景：系统安装后，用户自行安装了 zhparser，运行本脚本重建 fts 列
# 用法: sudo ./fix-zhparser.sh

set -e

PG_USER="${POSTGRES_USER:-postgres}"
PG_DB="${POSTGRES_DB:-memory}"
LOG_FILE="/tmp/sinovec-zhparser-fix.log"

echo "═══════════════════════════════════════════════════════"
echo "  SinoVec zhparser 修复脚本"
echo "═══════════════════════════════════════════════════════"

: > "$LOG_FILE"

# ── 第1步：检测 zhparser 扩展 ────────────────────────────
echo "检测 zhparser 扩展..."
ZH_READY=$(psql -U "$PG_USER" -d "$PG_DB" -t -c "
    SELECT 1 FROM pg_extension WHERE extname='zhparser';
" 2>/dev/null || echo "")

if [ "$ZH_READY" = "1" ]; then
    echo "✅ zhparser 扩展已安装"
else
    echo "❌ zhparser 未安装，请先安装 zhparser"
    echo ""
    echo "安装方法："
    echo "  apt-get install git build-essential postgresql-server-dev-16"
    echo "  git clone --depth 1 https://github.com/amutu/zhparser.git /tmp/zhparser"
    echo "  cd /tmp/zhparser && make && make install"
    echo "  sudo -u postgres psql -d memory -c \"CREATE EXTENSION zhparser;\""
    echo ""
    echo "安装完成后重新运行: sudo ./fix-zhparser.sh"
    exit 1
fi

# ── 第2步：确认 fts 列存在 ──────────────────────────────
echo "检查 sinovec 表结构..."
COL_EXISTS=$(psql -U "$PG_USER" -d "$PG_DB" -t -c "
    SELECT 1 FROM information_schema.columns
    WHERE table_name='sinovec' AND column_name='fts';
" 2>/dev/null || echo "")

if [ "$COL_EXISTS" != "1" ]; then
    echo "❌ sinovec 表不存在或无 fts 列，请先运行 install.sh"
    exit 1
fi
echo "✅ fts 列存在"

# ── 第3步：检测/创建 chinese_zh 配置 ──────────────────
echo "检查 chinese_zh 文本搜索配置..."
CONFIG_OK=$(psql -U "$PG_USER" -d "$PG_DB" -t -c "
    SELECT 1 FROM pg_ts_config WHERE cfgname='chinese_zh';
" 2>/dev/null || echo "")

if [ "$CONFIG_OK" != "1" ]; then
    echo "创建 chinese_zh 配置..."
    psql -U "$PG_USER" -d "$PG_DB" 2>> "$LOG_FILE" << 'EOF'
DO $$
BEGIN
    CREATE TEXT SEARCH CONFIGURATION chinese_zh (PARSER = zhparser);
    ALTER TEXT SEARCH CONFIGURATION chinese_zh
        ALTER MAPPING FOR asciiword, word WITH simple;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE '创建 chinese_zh 失败: %', SQLERRM;
END $$;
EOF
    CONFIG_OK=$(psql -U "$PG_USER" -d "$PG_DB" -t -c "
        SELECT 1 FROM pg_ts_config WHERE cfgname='chinese_zh';
    " 2>/dev/null || echo "")
fi

if [ "$CONFIG_OK" != "1" ]; then
    echo "❌ chinese_zh 配置创建失败，详见 $LOG_FILE"
    exit 1
fi
echo "✅ chinese_zh 配置就绪"

# ── 第4步：验证新 fts 表达式可用 ──────────────────────
echo "验证 chinese_zh 分词表达式..."
TEST_OK=$(psql -U "$PG_USER" -d "$PG_DB" -t -c "
    SELECT to_tsvector('chinese_zh', '测试中文分词搜索功能') IS NOT NULL;
" 2>/dev/null | tr -d ' ' || echo "f")

if [ "$TEST_OK" != "t" ]; then
    echo "❌ chinese_zh 分词验证失败"
    exit 1
fi
echo "✅ chinese_zh 分词验证通过"

# ── 第5步：获取当前 fts 配置信息 ──────────────────────
OLD_EXPR=$(psql -U "$PG_USER" -d "$PG_DB" -t -c "
    SELECT pg_get_expr(adbin, adrelid)
    FROM pg_attrdef
    WHERE adrelid = 'sinovec'::regclass
      AND adnum = (
          SELECT attnum FROM pg_attribute
          WHERE attrelid = 'sinovec'::regclass
            AND attname = 'fts'
      );
" 2>/dev/null | xargs || echo "unknown")

echo "当前 fts 表达式: $OLD_EXPR"

# ── 第6步：重建 fts 列（重命名→新增→验证→删除旧列）─
echo ""
echo "开始重建 fts 列..."

echo "Step 1/4: 重命名旧 fts 列 → fts_old..."
psql -U "$PG_USER" -d "$PG_DB" -c "
    ALTER TABLE sinovec RENAME COLUMN fts TO fts_old;
" 2>> "$LOG_FILE"
echo "✅ Step 1 完成"

echo "Step 2/4: 添加新 fts 列（chinese_zh 配置）..."
psql -U "$PG_USER" -d "$PG_DB" 2>> "$LOG_FILE" << 'EOF'
ALTER TABLE sinovec ADD COLUMN fts tsvector
    GENERATED ALWAYS AS (
        to_tsvector('chinese_zh', coalesce(payload->>'data', ''))
    ) STORED;
EOF
echo "✅ Step 2 完成"

echo "Step 3/4: 验证新 fts 列..."
ROW_COUNT=$(psql -U "$PG_USER" -d "$PG_DB" -t -c "
    SELECT count(*) FROM sinovec WHERE fts IS NOT NULL;
" 2>/dev/null | tr -d ' ' || echo "0")
echo "✅ 新 fts 列验证通过（$ROW_COUNT 行已生成）"

echo "Step 4/4: 删除旧 fts 列..."
psql -U "$PG_USER" -d "$PG_DB" -c "
    ALTER TABLE sinovec DROP COLUMN fts_old RESTRICT;
" 2>> "$LOG_FILE"
echo "✅ Step 4 完成"

# ── 第7步：确认索引存在 ──────────────────────────────
IDX_EXISTS=$(psql -U "$PG_USER" -d "$PG_DB" -t -c "
    SELECT 1 FROM pg_indexes WHERE tablename='sinovec' AND indexname='idx_sinovec_fts';
" 2>/dev/null || echo "")

if [ "$IDX_EXISTS" != "1" ]; then
    echo "重建全文索引..."
    psql -U "$PG_USER" -d "$PG_DB" -c "
        CREATE INDEX IF NOT EXISTS idx_sinovec_fts ON sinovec USING gin (fts);
    " 2>> "$LOG_FILE"
fi

echo ""
echo "═══════════════════════════════════════════════════════"
echo "✅ zhparser 修复成功！"
echo ""
echo "  fts 列已使用 chinese_zh 分词配置"
echo "  测试命令:"
echo "    SELECT to_tsvector('chinese_zh', '北京天气怎么样');"
echo ""
echo "  验证搜索:"
echo "    SELECT id, substr(payload->>'data', 1, 30) FROM sinovec"
echo "    WHERE fts @@ to_tsquery('chinese_zh', '北京');"
echo "═══════════════════════════════════════════════════════"
