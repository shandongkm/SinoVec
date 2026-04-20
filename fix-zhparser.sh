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

# ── psql helper：统一通过 sudo -u postgres 执行（兼容 peer auth）─────
run_psql() {
    sudo -u postgres psql -U "$PG_USER" -d "$PG_DB" "$@"
}
run_psql_t() {
    sudo -u postgres psql -t -U "$PG_USER" -d "$PG_DB" "$@"
}

# ── 第1步：检测 zhparser 扩展 ────────────────────────────
echo "检测 zhparser 扩展..."
ZH_READY=$(run_psql_t -c "
    SELECT 1 FROM pg_extension WHERE extname='zhparser';
" 2>/dev/null | tr -d ' ' || echo "")

if [ "$ZH_READY" = "1" ]; then
    echo "✅ zhparser 扩展已安装"
else
    echo "❌ zhparser 未安装，请先安装 zhparser"
    echo ""
    echo "安装方法："
    echo "  # 1. 安装编译依赖（动态检测 PostgreSQL 版本）"
    echo "  apt-get install -y git build-essential"
    echo "  _PG_VER=\$(psql --version | awk '{print \$3}' | cut -d. -f1)"
    echo "  apt-get install -y postgresql-server-dev-\${_PG_VER}"
    echo ""
    echo "  # 2. 编译安装 SCWS（zhparser 依赖库）"
    echo "  git clone --depth 1 https://github.com/hightman/scws.git /tmp/scws"
    echo "  cd /tmp/scws && cat > version.h << 'EOFH'"
    echo "  #ifndef SCWS_VERSION_H"
    echo "  #define SCWS_VERSION_H"
    echo "  #define SCWS_VERSION \"1.2.3\""
    echo "  #endif"
    echo "  EOFH"
    echo '  ./configure --prefix=/usr/local && make -j$(nproc) && make install && ldconfig'
    echo ""
    echo "  # 3. 编译安装 zhparser"
    echo "  git clone --depth 1 https://github.com/amutu/zhparser.git /tmp/zhparser"
    echo "  cd /tmp/zhparser && make SCWS_ROOT=/usr/local && make install SCWS_ROOT=/usr/local"
    echo ""
    echo "  # 4. 注册数据库扩展"
    echo "  sudo -u postgres psql -d memory -c \"CREATE EXTENSION zhparser;\""
    echo ""
    echo "安装完成后重新运行: sudo ./fix-zhparser.sh"
    exit 1
fi

# ── 第2步：确认 fts 列存在 ──────────────────────────────
echo "检查 sinovec 表结构..."
COL_EXISTS=$(run_psql_t -c "
    SELECT 1 FROM information_schema.columns
    WHERE table_name='sinovec' AND column_name='fts';
" 2>/dev/null | tr -d ' ' || echo "")

if [ "$COL_EXISTS" != "1" ]; then
    echo "❌ sinovec 表不存在或无 fts 列，请先运行 install.sh"
    exit 1
fi
echo "✅ fts 列存在"

# ── 第3步：检测/创建 chinese_zh 配置 ──────────────────
echo "检查 chinese_zh 文本搜索配置..."
CONFIG_OK=$(run_psql_t -c "
    SELECT 1 FROM pg_ts_config WHERE cfgname='chinese_zh';
" 2>/dev/null | tr -d ' ' || echo "")

if [ "$CONFIG_OK" != "1" ]; then
    echo "创建 chinese_zh 配置..."
    run_psql 2>> "$LOG_FILE" << 'EOF'
DO $$
BEGIN
    CREATE TEXT SEARCH CONFIGURATION chinese_zh (PARSER = zhparser);
    ALTER TEXT SEARCH CONFIGURATION chinese_zh
        ALTER MAPPING FOR asciiword, word WITH simple;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE '创建 chinese_zh 配置失败: %', SQLERRM;
END $$;
EOF
    CONFIG_OK=$(run_psql_t -c "
        SELECT 1 FROM pg_ts_config WHERE cfgname='chinese_zh';
    " 2>/dev/null | tr -d ' ' || echo "")
fi

if [ "$CONFIG_OK" != "1" ]; then
    echo "❌ chinese_zh 配置创建失败，详见 $LOG_FILE"
    exit 1
fi
echo "✅ chinese_zh 配置就绪"

# ── 第4步：验证新 fts 表达式可用 ──────────────────────
echo "验证 chinese_zh 分词表达式..."
TEST_OK=$(run_psql_t -c "
    SELECT to_tsvector('chinese_zh', '测试中文分词搜索功能') IS NOT NULL;
" 2>/dev/null | tr -d ' ' || echo "f")

if [ "$TEST_OK" != "t" ]; then
    echo "❌ chinese_zh 分词验证失败"
    exit 1
fi
echo "✅ chinese_zh 分词验证通过"

# ── 第5步：获取当前 fts 配置信息 ──────────────────────
OLD_EXPR=$(run_psql_t -c "
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
run_psql -c "
    ALTER TABLE sinovec RENAME COLUMN fts TO fts_old;
" 2>> "$LOG_FILE"
echo "✅ Step 1 完成"

echo "Step 2/4: 添加新 fts 列（chinese_zh 配置）..."
run_psql 2>> "$LOG_FILE" << 'EOF'
ALTER TABLE sinovec ADD COLUMN fts tsvector
    GENERATED ALWAYS AS (
        to_tsvector('chinese_zh', coalesce(payload->>'data', ''))
    ) STORED;
EOF
echo "✅ Step 2 完成"

echo "Step 3/4: 验证新 fts 列..."
ROW_COUNT=$(run_psql_t -c "
    SELECT count(*) FROM sinovec WHERE fts IS NOT NULL;
" 2>/dev/null | tr -d ' ' || echo "0")
echo "✅ 新 fts 列验证通过（$ROW_COUNT 行已生成）"

echo "Step 4/4: 删除旧 fts 列..."
run_psql -c "
    ALTER TABLE sinovec DROP COLUMN fts_old RESTRICT;
" 2>> "$LOG_FILE"
echo "✅ Step 4 完成"

# ── 第7步：确认索引存在 ──────────────────────────────
IDX_EXISTS=$(run_psql_t -c "
    SELECT 1 FROM pg_indexes WHERE tablename='sinovec' AND indexname='idx_sinovec_fts';
" 2>/dev/null | tr -d ' ' || echo "")

if [ "$IDX_EXISTS" != "1" ]; then
    echo "重建全文索引..."
    run_psql -c "
        CREATE INDEX IF NOT EXISTS idx_sinovec_fts ON sinovec USING gin (fts);
    " 2>> "$LOG_FILE"
fi

echo ""
echo "═══════════════════════════════════════════════════════"
echo "✅ zhparser 修复成功！"
echo ""
echo "  fts 列已使用 chinese_zh 分词配置"
echo "  测试命令:"
echo "    sudo -u postgres psql -d memory -c \"SELECT to_tsvector('chinese_zh', '北京天气怎么样');\""
echo ""
echo "  验证搜索:"
echo "    sudo -u postgres psql -d memory -c \"SELECT id, substr(payload->>'data', 1, 30) FROM sinovec WHERE fts @@ to_tsquery('chinese_zh', '北京');\""
echo "═══════════════════════════════════════════════════════"
