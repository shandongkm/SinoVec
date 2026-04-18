#!/bin/bash
# zhparser 初始化脚本（供 Docker 初始化使用）
# 功能：检测/编译安装 zhparser，创建 chinese_zh 配置，添加 fts 列
# 失败时：降级到 simple，不阻塞容器启动，打印详细原因和解决方案

PG_USER="${POSTGRES_USER:-postgres}"
PG_DB="${POSTGRES_DB:-memory}"
INSTALL_LOG="/var/lib/postgresql/init-zhparser.log"
TS_CONFIG=""   # 最终使用的分词配置

echo "=== SinoVec zhparser 初始化开始 ==="

# 初始化日志文件
: > "$INSTALL_LOG"

# ── 第1步：检测 zhparser 扩展 ────────────────────────────
ZH_READY=$(psql -U "$PG_USER" -d "$PG_DB" -t -c "
    SELECT 1 FROM pg_extension WHERE extname='zhparser';
" 2>/dev/null || echo "")

if [ "$ZH_READY" = "1" ]; then
    echo "✅ zhparser 扩展已安装"
    TS_CONFIG="chinese_zh"
else
    echo "⚠️  zhparser 未安装，尝试编译安装..."
    ZH_INSTALLED=false

    # ── 第2步：编译安装 zhparser ──────────────────────────
    echo "安装编译依赖..."

    # 安装 git 和 build-essential（基本都有，但确保一下）
    if ! command -v git &> /dev/null; then
        apt-get install -y git 2>> "$INSTALL_LOG" || {
            echo "[FAIL] apt-get install git 失败: $?" | tee -a "$INSTALL_LOG"
        }
    fi

    # 检查 postgresql-server-dev 是否存在
    PG_DEV=""
    for ver in 16 15 14 13; do
        if dpkg -l "postgresql-server-dev-$ver" 2>/dev/null | grep -q "^ii"; then
            PG_DEV="postgresql-server-dev-$ver"
            break
        fi
    done

    if [ -z "$PG_DEV" ]; then
        echo "安装 postgresql-server-dev..."
        if apt-get install -y postgresql-server-dev-16 2>> "$INSTALL_LOG"; then
            PG_DEV="postgresql-server-dev-16"
        else
            echo "[FAIL] apt-get install postgresql-server-dev-16 失败，尝试 15..." | tee -a "$INSTALL_LOG"
            if apt-get install -y postgresql-server-dev-15 2>> "$INSTALL_LOG"; then
                PG_DEV="postgresql-server-dev-15"
            else
                echo "[FAIL] apt-get install postgresql-server-dev-15 也失败" | tee -a "$INSTALL_LOG"
                PG_DEV=""
            fi
        fi
    else
        echo "✅ 找到 $PG_DEV"
    fi

    # 尝试编译 zhparser
    echo "编译 zhparser（详细日志: $INSTALL_LOG）..."
    cd /tmp
    rm -rf zhparser 2>/dev/null || true

    CLONE_OK=false
    git clone --depth 1 https://github.com/amutu/zhparser.git 2>> "$INSTALL_LOG" && CLONE_OK=true

    if [ "$CLONE_OK" = "true" ]; then
        cd zhparser
        echo "[INFO] 执行 make..." >> "$INSTALL_LOG"
        make clean >> "$INSTALL_LOG" 2>&1 || true
        make >> "$INSTALL_LOG" 2>&1 && make install >> "$INSTALL_LOG" 2>&1 && {
            echo "[OK] zhparser make && make install 成功"
            ZH_INSTALLED=true
        } || {
            echo "[FAIL] zhparser make 或 make install 失败" | tee -a "$INSTALL_LOG"
            echo "--- Make 输出 ---" >> "$INSTALL_LOG"
            cat /tmp/zhparser/make.log 2>/dev/null >> "$INSTALL_LOG" || true
        }
    else
        echo "[FAIL] git clone zhparser 失败（网络或 GitHub 访问问题）" | tee -a "$INSTALL_LOG"
        echo "可能原因：GitHub 访问被阻断、git 未安装、网络代理问题" | tee -a "$INSTALL_LOG"
    fi

    rm -rf /tmp/zhparser
    cd /

    # ── 第3步：注册数据库扩展 ──────────────────────────
    if [ "$ZH_INSTALLED" = "true" ]; then
        echo "注册 zhparser 数据库扩展..."
        psql -U "$PG_USER" -d "$PG_DB" -c "CREATE EXTENSION IF NOT EXISTS zhparser;" 2>> "$INSTALL_LOG" && {
            echo "✅ zhparser 数据库扩展注册成功"
            TS_CONFIG="chinese_zh"
        } || {
            echo "[FAIL] zhparser 数据库扩展注册失败" | tee -a "$INSTALL_LOG"
            psql -U "$PG_USER" -d "$PG_DB" -c "CREATE EXTENSION IF NOT EXISTS zhparser;" 2>&1 | tail -5 >> "$INSTALL_LOG"
        }
    fi
fi

# ── 第4步：创建 chinese_zh 配置 ──────────────────────────
if [ "$TS_CONFIG" = "chinese_zh" ]; then
    CONFIG_EXISTS=$(psql -U "$PG_USER" -d "$PG_DB" -t -c "
        SELECT 1 FROM pg_ts_config WHERE cfgname='chinese_zh';
    " 2>/dev/null || echo "")

    if [ "$CONFIG_EXISTS" != "1" ]; then
        echo "创建 chinese_zh 文本搜索配置..."
        psql -U "$PG_USER" -d "$PG_DB" << 'EOF' 2>> "$INSTALL_LOG" || true
DO $$
BEGIN
    CREATE TEXT SEARCH CONFIGURATION chinese_zh (PARSER = zhparser);
    ALTER TEXT SEARCH CONFIGURATION chinese_zh
        ALTER MAPPING FOR asciiword, word WITH simple;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE '创建 chinese_zh 配置失败: %', SQLERRM;
END $$;
EOF
        # 再次检查
        CONFIG_EXISTS=$(psql -U "$PG_USER" -d "$PG_DB" -t -c "
            SELECT 1 FROM pg_ts_config WHERE cfgname='chinese_zh';
        " 2>/dev/null || echo "")
    fi

    if [ "$CONFIG_EXISTS" != "1" ]; then
        echo "[FAIL] chinese_zh 配置创建失败" | tee -a "$INSTALL_LOG"
        TS_CONFIG=""
    fi
fi

# ── 第5步：降级决定 ─────────────────────────────────────
if [ -z "$TS_CONFIG" ]; then
    echo "⚠️  zhparser 不可用，降级使用 simple 分词配置"
    TS_CONFIG="simple"
    ZH_STATUS="未安装"
elif [ "$TS_CONFIG" = "chinese_zh" ]; then
    ZH_STATUS="已安装"
fi

# ── 第6步：添加 fts 列 ──────────────────────────────────
FTS_EXISTS=$(psql -U "$PG_USER" -d "$PG_DB" -t -c "
    SELECT 1 FROM information_schema.columns
    WHERE table_name='sinovec' AND column_name='fts';
" 2>/dev/null || echo "")

if [ "$FTS_EXISTS" = "1" ]; then
    echo "✅ fts 列已存在，跳过"
else
    echo "添加 fts 全文检索列（使用 ${TS_CONFIG} 配置）..."
    psql -U "$PG_USER" -d "$PG_DB" << EOF
ALTER TABLE sinovec ADD COLUMN fts tsvector
    GENERATED ALWAYS AS (
        to_tsvector('${TS_CONFIG}', coalesce(payload->>'data', ''))
    ) STORED;
CREATE INDEX IF NOT EXISTS idx_sinovec_fts ON sinovec USING gin (fts);
EOF
    # 验证 fts 列已创建且数据已生成
    FTS_VERIFY=$(psql -U "$PG_USER" -d "$PG_DB" -t -c "
        SELECT count(*) FROM information_schema.columns
        WHERE table_name='sinovec' AND column_name='fts' AND is_nullable = 'NO';
    " 2>/dev/null | tr -d ' ' || echo "0")
    if [ "$FTS_VERIFY" = "1" ]; then
        echo "✅ fts 列创建成功（已验证，${TS_CONFIG} 配置）"
    else
        echo "⚠️  fts 列创建结果未知，请手动验证"
    fi
fi

# ── 第7步：打印结果报告 ───────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  SinoVec zhparser 初始化报告"
echo "═══════════════════════════════════════════════════════"
echo "zhparser 状态: $ZH_STATUS"
echo "分词配置:      $TS_CONFIG"
echo ""

if [ "$ZH_STATUS" = "已安装" ]; then
    echo "✅ zhparser 安装成功！"
    echo "   全文检索使用 chinese_zh 中文分词"
elif [ -f "$INSTALL_LOG" ] && [ -s "$INSTALL_LOG" ]; then
    echo "⚠️  zhparser 安装失败，已降级为 simple 分词"
    echo ""
    echo "── 失败原因（$INSTALL_LOG）──"
    grep -E "^\[FAIL\]|^\[INFO\]" "$INSTALL_LOG" | head -20
    echo ""
    echo "── 解决方案 ──────────────────────────────────────"
    echo ""
    echo "方法一（推荐 - 主机安装 zhparser 后修复）："
    echo "   # 1. 在主机安装 zhparser（编译安装）"
    echo "   apt-get install git build-essential postgresql-server-dev-16"
    echo "   git clone --depth 1 https://github.com/amutu/zhparser.git /tmp/zhparser"
    echo "   cd /tmp/zhparser && make && make install"
    echo "   sudo -u postgres psql -d memory -c \"CREATE EXTENSION zhparser;\""
    echo ""
    echo "   # 2. 运行修复脚本"
    echo "   cp SinoVec/fix-zhparser.sh /tmp/"
    echo "   sudo /tmp/fix-zhparser.sh"
    echo ""
    echo "方法二（无需 zhparser）："
    echo "   向量检索（embedding）不依赖 zhparser，可正常使用"
    echo "   BM25 搜索将使用 simple 分词，对中文按字符分词"
    echo ""
    echo "详细日志: cat $INSTALL_LOG"
else
    echo "⚠️  zhparser 安装状态未知，使用 $TS_CONFIG 分词"
fi
echo "═══════════════════════════════════════════════════════"
echo "=== zhparser 初始化完成 ==="
