#!/bin/bash
#
# SinoVec 安装脚本
# 用法: ./install.sh [安装目录]
#        ./install.sh --venv [venv路径] [安装目录]
# 默认安装目录: /opt/SinoVec
#

set -e

# 默认值（与 memory_sinovec.py 保持一致）
DEFAULT_DB_PORT=5433
DEFAULT_DB_USER=sinovec

# ── 服务运行用户（systemd User= 字段）───────────────────────────
# 生产环境建议使用非 root 用户以减少攻击面。
# 注意：使用非 root 用户时，需确保：
#   1. 该用户可读取 /etc/default/sinovec（chmod +r /etc/default/sinovec）
#   2. 该用户可读写 $PREFIX/workspace（若启用自动记忆层级功能）
#   3. 该用户的 ulimit 足够（systemd 默认已设）
SERVICE_USER="root"


# ── 解析参数 ──────────────────────────────────────────────
USE_VENV=false
VENV_PATH=""
INSTALL_PREFIX=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --venv)
            USE_VENV=true
            if [[ -n "$2" && "${2:0:1}" != "-" ]]; then
                VENV_PATH="$2"
                shift
            fi
            ;;
        -*)
            echo "未知选项: $1"
            echo "用法: $0 [--venv [venv路径]] [安装目录]"
            exit 1
            ;;
        *)
            INSTALL_PREFIX="$1"
            ;;
    esac
    shift
done

PREFIX="${INSTALL_PREFIX:-/opt/SinoVec}"
CURRENT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 虚拟环境 python 路径
if [ "$USE_VENV" = true ]; then
    VENV_PATH="${VENV_PATH:-$PREFIX/venv}"
    PYTHON_CMD="$VENV_PATH/bin/python3"
    PIP_CMD="$VENV_PATH/bin/pip3"
    if [ ! -d "$VENV_PATH" ]; then
        echo "创建虚拟环境: $VENV_PATH"
        python3 -m venv "$VENV_PATH"
    fi
    echo "✅ 虚拟环境: $VENV_PATH"
else
    PYTHON_CMD="python3"
    PIP_CMD="pip3"
fi

echo "========================================="
echo "  SinoVec 安装脚本"
echo "========================================="
echo "安装目录: $PREFIX"
echo "Python: $PYTHON_CMD"

# ── 检查 root 权限 ──────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo "错误: 请使用 sudo 或以 root 用户运行"
    exit 1
fi

# ── 检查 Python ─────────────────────────────────────────────
if ! command -v python3 &> /dev/null; then
    echo "错误: 未安装 Python3"
    exit 1
fi
_PY_VER=$($PYTHON_CMD -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)
if [ -z "$_PY_VER" ]; then
    echo "错误: 无法读取 Python 版本"
    exit 1
fi
# SinoVec 需要 Python 3.9+（使用 datetime.fromisoformat 的 timezone 支持等特性）
_PY_MAJOR=$(echo "$_PY_VER" | cut -d. -f1)
_PY_MINOR=$(echo "$_PY_VER" | cut -d. -f2)
if [ "$_PY_MAJOR" -lt 3 ] || [ "$_PY_MAJOR" -eq 3 && [ "$_PY_MINOR" -lt 9 ]; then
    echo "错误: SinoVec 需要 Python 3.9+，当前版本: $_PY_VER"
    exit 1
fi
echo "Python 版本: $_PY_VER ✅"

# ── 检查 PostgreSQL ─────────────────────────────────────────
if ! command -v psql &> /dev/null; then
    echo "错误: 未安装 PostgreSQL"
    echo "Ubuntu/Debian: sudo apt install postgresql"
    echo "CentOS/RHEL:   sudo yum install postgresql-server"
    exit 1
fi
echo "PostgreSQL 版本: $(psql --version | awk '{print $3}')"

# ── 检查 pgvector ────────────────────────────────────────────
# 动态检测 PostgreSQL 大版本号（兼容 14/15/16/17+）
_PG_VERSION=$(psql --version | awk '{print $3}' | cut -d. -f1)
if sudo -u postgres psql -c "SELECT * FROM pg_extension WHERE extname='vector';" 2>/dev/null | grep -q vector; then
    echo "✅ pgvector 扩展已安装"
else
    echo "⚠️  pgvector 扩展未安装，正在安装..."
    apt update
    if apt-cache show "postgresql-${_PG_VERSION}-pgvector" &>/dev/null; then
        apt install -y "postgresql-${_PG_VERSION}-pgvector"
    else
        for _v in 17 16 15 14; do
            if apt-cache show "postgresql-${_v}-pgvector" &>/dev/null; then
                apt install -y "postgresql-${_v}-pgvector"
                break
            fi
        done
    fi
    sudo -u postgres psql -c "CREATE EXTENSION IF NOT EXISTS vector;"
fi

# ── 数据库配置（默认值与 memory_sinovec.py 一致）─────────────
# 端口验证：确保用户输入的端口有 PostgreSQL 在监听
_current_pg_port=$(sudo -u postgres psql -t -c 'SHOW port;' 2>/dev/null | tr -d ' ')
_valid_port=false
while [ "$_valid_port" = "false" ]; do
    read -p "数据库端口 [$DEFAULT_DB_PORT]: " DB_PORT
    DB_PORT="${DB_PORT:-$DEFAULT_DB_PORT}"
    # 验证端口是否可达（TCP 握手测试）
    if timeout 2 bash -c "echo >/dev/tcp/127.0.0.1/$DB_PORT" 2>/dev/null; then
        echo "✅ 端口 $DB_PORT 可达"
        _valid_port=true
    else
        echo "⚠️  端口 $DB_PORT 不可访问"
        echo "   PostgreSQL 当前监听端口: $_current_pg_port"
        echo "   请输入正确的端口号，或直接回车使用默认值 [$DEFAULT_DB_PORT]"
    fi
done

read -p "数据库用户 [$DEFAULT_DB_USER]: " DB_USER
DB_USER=${DB_USER:-$DEFAULT_DB_USER}
# PostgreSQL identifier 验证：仅允许字母、数字、下划线
if [[ ! "$DB_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
    echo "错误: 用户名只能包含字母、数字和下划线，且不能以数字开头" >&2
    exit 1
fi

read -sp "数据库密码: " DB_PASS
echo ""

if [ -z "$DB_PASS" ]; then
    echo "错误: 密码不能为空"
    exit 1
fi

read -p "数据库名称 [memory]: " DB_NAME
DB_NAME=${DB_NAME:-memory}
# PostgreSQL identifier 验证：仅允许字母、数字、下划线（且首字符不能是数字）
if [[ ! "$DB_NAME" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
    echo "错误: 数据库名称只能包含字母、数字和下划线，且不能以数字开头" >&2
    exit 1
fi

# ── 创建数据库和用户 ─────────────────────────────────────────
echo "配置数据库..."

sudo -u postgres psql -c "CREATE DATABASE $DB_NAME;" 2>/dev/null || echo "数据库 $DB_NAME 已存在"

if sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
    echo "用户 $DB_USER 已存在"
else
    sudo -u postgres psql --set="DB_PASS=$DB_PASS" -c "CREATE USER $DB_USER WITH PASSWORD :'DB_PASS';"
fi
sudo -u postgres psql -c "ALTER USER $DB_USER WITH SUPERUSER;"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"

# ── 导入表结构 ──────────────────────────────────────────────
echo "导入数据库表结构..."
PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" -f "$CURRENT_DIR/rebuild_memory_sinovec.sql"
echo "✅ 表结构已创建"

# ── zhparser 可选安装 ──────────────────────────────────────
ZH_INSTALL_LOG="/tmp/zhparser-install.log"
ZH_INSTALLED=false
read -p "是否安装 zhparser（中文分词插件，提升搜索精度）？[Y/n]: " INSTALL_ZHPARSER
INSTALL_ZHPARSER="${INSTALL_ZHPARSER:-Y}"

if [[ "$INSTALL_ZHPARSER" =~ ^[Yy]$ ]]; then
    echo "正在安装 zhparser（详细日志: $ZH_INSTALL_LOG）..."
    : > "$ZH_INSTALL_LOG"

    # 检查编译依赖
    if ! command -v git &> /dev/null; then
        echo "安装 git..."
        apt-get install -y git 2>> "$ZH_INSTALL_LOG" || true
    fi
    if ! command -v make &> /dev/null; then
        echo "安装 build-essential..."
        apt-get install -y build-essential 2>> "$ZH_INSTALL_LOG" || true
    fi

    # 动态查找已安装的 postgresql-server-dev（支持 17/16/15/14）
    _PG_MAJOR=$(psql --version | awk '{print $3}' | cut -d. -f1)
    PG_DEV=""
    for ver in ${_PG_MAJOR} 16 15 14 13; do
        if dpkg -l "postgresql-server-dev-${ver}" 2>/dev/null | grep -q "^ii"; then
            PG_DEV="postgresql-server-dev-${ver}"
            break
        fi
    done
    if [ -z "$PG_DEV" ]; then
        echo "安装 postgresql-server-dev..."
        apt-get install -y "postgresql-server-dev-${_PG_MAJOR}" 2>> "$ZH_INSTALL_LOG" || {
            for _v in 16 15 14 13; do
                if apt-get install -y "postgresql-server-dev-${_v}" 2>> "$ZH_INSTALL_LOG"; then
                    PG_DEV="postgresql-server-dev-${_v}"
                    break
                fi
            done
        }
    fi
    if [ -n "$PG_DEV" ]; then
        echo "✅ 找到 $PG_DEV"
    fi

    # ── 第1步：编译安装 SCWS（zhparser 依赖库）──────────────
    echo "[INFO] 编译安装 SCWS（zhparser 依赖）..." >> "$ZH_INSTALL_LOG"
    cd /tmp
    rm -rf scws scws-install 2>/dev/null || true

    _SCWS_INSTALLED=false
    if git clone --depth 1 https://github.com/hightman/scws.git scws-src 2>> "$ZH_INSTALL_LOG"; then
        cd scws-src
        cat > version.h << 'EOFH' 2>> "$ZH_INSTALL_LOG"
#ifndef SCWS_VERSION_H
#define SCWS_VERSION_H
#define SCWS_VERSION "1.2.3"
#define SCWS_VERSION_NUM 0x010203
#define SCWS_VERSION_MAJOR 1
#define SCWS_VERSION_MINOR 2
#define SCWS_VERSION_REV 3
#endif
EOFH
        ./configure --prefix=/usr/local 2>> "$ZH_INSTALL_LOG" && \
        make -j$(nproc) 2>> "$ZH_INSTALL_LOG" && \
        make install 2>> "$ZH_INSTALL_LOG" && \
        ldconfig 2>> "$ZH_INSTALL_LOG" && \
        _SCWS_INSTALLED=true && \
        echo "✅ SCWS 编译安装成功" || \
        echo "⚠️  SCWS 安装失败，详见 $ZH_INSTALL_LOG"
        cd /tmp
        rm -rf scws-src
    else
        echo "⚠️  git clone SCWS 失败，详见 $ZH_INSTALL_LOG"
    fi

    ldconfig 2>/dev/null || true

    # ── 第2步：编译安装 zhparser（依赖 SCWS）─────────────────
    cd /tmp
    rm -rf zhparser 2>/dev/null || true
    echo "[INFO] git clone zhparser..." >> "$ZH_INSTALL_LOG"

    if git clone --depth 1 https://github.com/amutu/zhparser.git 2>> "$ZH_INSTALL_LOG"; then
        cd zhparser
        make clean >> "$ZH_INSTALL_LOG" 2>&1 || true
        if make SCWS_ROOT=/usr/local >> "$ZH_INSTALL_LOG" 2>&1 && make install SCWS_ROOT=/usr/local >> "$ZH_INSTALL_LOG" 2>&1; then
            ZH_INSTALLED=true
            echo "✅ zhparser 编译安装成功"
        else
            echo "⚠️  zhparser make/make install 失败，详见 $ZH_INSTALL_LOG"
            if [ "$_SCWS_INSTALLED" = "false" ]; then
                echo "   提示：SCWS 依赖库安装失败可能是根本原因"
            fi
        fi
    else
        echo "⚠️  git clone zhparser 失败，详见 $ZH_INSTALL_LOG"
    fi
    rm -rf /tmp/zhparser
    cd /

    # 注册数据库扩展
    if [ "$ZH_INSTALLED" = "true" ]; then
        echo "注册 zhparser 数据库扩展..."
        if sudo -u postgres psql -c "CREATE EXTENSION IF NOT EXISTS zhparser;" 2>> "$ZH_INSTALL_LOG"; then
            echo "✅ zhparser 数据库扩展注册成功"
        else
            echo "⚠️  zhparser 扩展注册失败，详见 $ZH_INSTALL_LOG"
            ZH_INSTALLED=false
        fi
    fi
fi

# ── 创建 chinese_zh 配置 + 添加 fts 列 ──────────────────
echo "配置全文检索列..."
TS_CONFIG="simple"
if [ "$ZH_INSTALLED" = "true" ]; then
    CONFIG_OK=$(sudo -u postgres psql -t -c "
        SELECT 1 FROM pg_ts_config WHERE cfgname='chinese_zh';
    " 2>/dev/null || echo "")
    if [ "$CONFIG_OK" != "1" ]; then
        echo "创建 chinese_zh 文本搜索配置..."
        sudo -u postgres psql << 'ZHPARSER_EOF' 2>> "$ZH_INSTALL_LOG" || true
DO $$
BEGIN
    CREATE TEXT SEARCH CONFIGURATION chinese_zh (PARSER = zhparser);
    ALTER TEXT SEARCH CONFIGURATION chinese_zh
        ALTER MAPPING FOR asciiword, word WITH simple;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE '创建 chinese_zh 配置失败: %', SQLERRM;
END $$;
ZHPARSER_EOF
    fi
    CONFIG_OK=$(sudo -u postgres psql -t -c "
        SELECT 1 FROM pg_ts_config WHERE cfgname='chinese_zh';
    " 2>/dev/null || echo "")
    [ "$CONFIG_OK" = "1" ] && TS_CONFIG="chinese_zh"
fi

if [ "$ZH_INSTALLED" != "true" ]; then
    echo "⚠️  zhparser 未安装，使用 simple 分词配置"
    echo "   后期安装 zhparser 后运行: sudo $PREFIX/fix-zhparser.sh"
fi

# 添加 fts 列（zhparser 不可用时自动降级到 simple）
PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" << EOFTS
DO \$\$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='sinovec' AND column_name='fts'
    ) THEN
        ALTER TABLE sinovec ADD COLUMN fts tsvector
            GENERATED ALWAYS AS (
                to_tsvector('${TS_CONFIG}', coalesce(payload->>'data', ''))
            ) STORED;
        CREATE INDEX IF NOT EXISTS idx_sinovec_fts ON sinovec USING gin (fts);
        RAISE NOTICE 'fts 列创建成功（使用 ${TS_CONFIG} 配置）';
    ELSE
        RAISE NOTICE 'fts 列已存在，跳过';
    END IF;
END \$\$;
EOFTS
echo "✅ fts 列配置完成（${TS_CONFIG}）"

# ── 安装 Python 依赖 ────────────────────────────────────────
echo "安装 Python 依赖..."
if [ "$USE_VENV" = true ]; then
    $PIP_CMD install -r "$CURRENT_DIR/requirements.txt"
else
    $PIP_CMD install -r "$CURRENT_DIR/requirements.txt" --break-system-packages 2>/dev/null \
        || $PIP_CMD install -r "$CURRENT_DIR/requirements.txt"
fi

# ── Ollama 可选安装 ────────────────────────────────────────
OLLAMA_MODEL_SELECTED=""
OLLAMA_INSTALLED_NOW=false

if command -v ollama &> /dev/null; then
    echo "✅ Ollama 已安装 ($(ollama version 2>/dev/null | head -1))"
    _ollama_installed=true
else
    _ollama_installed=false
    echo "Ollama 可选安装（用于 LLM 查询扩展和结果重排）"
    echo "  注意：安装需要访问 github.com（国内可能需要代理）"
    read -p "是否现在安装 Ollama？[y/N]: " INSTALL_OLLAMA
    if [[ "$INSTALL_OLLAMA" =~ ^[Yy]$ ]]; then
        echo "正在安装 Ollama（下载约 50MB 安装包）..."
        curl -fsSL --max-time 120 https://ollama.com/install.sh -o /tmp/ollama_install.sh
        _curl_exit=$?
        if [ $_curl_exit -ne 0 ]; then
            echo "⚠️  Ollama 安装脚本下载失败（curl exit=$_curl_exit），跳过安装"
            echo "   手动安装: curl -fsSL https://ollama.com/install.sh | sh"
        elif [ ! -s /tmp/ollama_install.sh ]; then
            echo "⚠️  Ollama 安装脚本为空（下载不完整），跳过安装"
        else
            sh /tmp/ollama_install.sh && OLLAMA_INSTALLED_NOW=true || {
                echo "⚠️  Ollama 安装失败，请检查网络或手动安装"
                echo "   手动安装: curl -fsSL https://ollama.com/install.sh | sh"
            }
        fi
        rm -f /tmp/ollama_install.sh
    else
        echo "⚠️  跳过 Ollama 安装，LLM 扩展功能将自动降级"
    fi
fi

# 模型选择（仅当 Ollama 已安装，或用户选择安装 Ollama 后才进入此步）
if command -v ollama &> /dev/null; then
    echo ""
    echo "请选择 Ollama 模型（决定向量检索精度）："
    echo "  1) qwen2.5:7b（推荐，精度高，显存占用约 4-6GB）"
    echo "  2) qwen2.5:3b（轻量，显存占用约 2-3GB，速度快）"
    echo "  3) qwen2.5:1.5b（极轻，省资源，适合内存有限机器）"
    echo "  4) 暂不拉取模型（后期手动 ollama pull）"
    read -p "选择 [1-4，默认 1]: " MODEL_CHOICE
    case "$MODEL_CHOICE" in
        2)  OLLAMA_MODEL_SELECTED="qwen2.5:3b" ;;
        3)  OLLAMA_MODEL_SELECTED="qwen2.5:1.5b" ;;
        4)  echo "可后期手动拉取: ollama pull qwen2.5:7b" ;;
        *)  OLLAMA_MODEL_SELECTED="qwen2.5:7b" ;;
    esac

    if [ -n "$OLLAMA_MODEL_SELECTED" ]; then
        # 仅在新安装 Ollama 时自动拉取，已有模型时跳过
        if [ "$OLLAMA_INSTALLED_NOW" = "true" ]; then
            echo "正在拉取模型 $OLLAMA_MODEL_SELECTED（首次约需 2-8 分钟，请耐心等待）..."
            if ollama pull "$OLLAMA_MODEL_SELECTED"; then
                echo "✅ 模型拉取完成"
            else
                echo "⚠️  模型拉取失败，请稍后运行: ollama pull $OLLAMA_MODEL_SELECTED"
            fi
        else
            # Ollama 已存在，检查模型是否已拉取
            if ollama list 2>/dev/null | grep -q "$OLLAMA_MODEL_SELECTED"; then
                echo "✅ 模型 $OLLAMA_MODEL_SELECTED 已存在"
            else
                echo "⚠️  模型 $OLLAMA_MODEL_SELECTED 未拉取"
                read -p "是否现在拉取？[Y/n]: " PULL_NOW
                PULL_NOW="${PULL_NOW:-Y}"
                if [[ "$PULL_NOW" =~ ^[Yy]$ ]]; then
                    echo "正在拉取（首次约需 2-8 分钟）..."
                    ollama pull "$OLLAMA_MODEL_SELECTED" && echo "✅ 完成" || echo "⚠️  失败，手动运行: ollama pull $OLLAMA_MODEL_SELECTED"
                fi
            fi
        fi
    fi
fi

# ── 复制代码到安装目录 ─────────────────────────────────────
echo "安装代码到 $PREFIX..."
mkdir -p "$PREFIX"
cp -r "$CURRENT_DIR"/. "$PREFIX/"
# 安装后清理 __pycache__/.pyc（避免跨 Python 版本的缓存污染）
find "$PREFIX" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "$PREFIX" -name '*.pyc' -delete 2>/dev/null || true

# ── 备份已有配置 ───────────────────────────────────────────
if [ -f /etc/default/sinovec ]; then
    echo "备份已有配置: /etc/default/sinovec.bak"
    cp /etc/default/sinovec /etc/default/sinovec.bak
fi

# ── 生成 API 密钥 ──────────────────────────────────────────
MEMORY_API_KEY=$(openssl rand -hex 16 2>/dev/null \
    || python3 -c "import secrets; print(secrets.token_hex(16))" \
    || printf 'sinovec_%08x%08x' $RANDOM $RANDOM $RANDOM)
echo "API Key: $MEMORY_API_KEY"

# ── 生成环境变量文件 ────────────────────────────────────────
echo "创建环境变量配置 /etc/default/sinovec..."
cat > /etc/default/sinovec << EOF
# SinoVec 环境变量（由 install.sh 自动生成）
# 上次备份: /etc/default/sinovec.bak

# 安装路径（供 systemd service 使用）
SINOVEC_HOME="$PREFIX"

# Python 虚拟环境（仅在使用 --venv 时生效）
# VENV_HOME="$VENV_PATH"

# 数据库连接配置（memory_sinovec.py 从环境变量读取）
MEMORY_DB_HOST=127.0.0.1
MEMORY_DB_PORT=$DB_PORT
MEMORY_DB_NAME=$DB_NAME
MEMORY_DB_USER=$DB_USER
MEMORY_DB_PASS=$DB_PASS

# HuggingFace 代理（国内用户需要则取消注释）
# HF_HUB_PROXY=http://127.0.0.1:7890

# Ollama LLM 配置（可选，用于查询扩展和结果重排）
# 不安装 Ollama 时自动降级（仅向量+BM25 检索）
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=${OLLAMA_MODEL_SELECTED:-qwen2.5:7b}
OLLAMA_FALLBACK_MODELS=qwen2.5:3b

# ── 服务配置 ────────────────────────────────────────────────
MEMORY_API_KEY=${MEMORY_API_KEY}
MEMORY_API_PORT=18793
DEFAULT_DB_PORT=${DB_PORT:-5433}
EOF
chmod 600 /etc/default/sinovec

# ── 生成普通用户可读的 skill 配置（供 OpenClaw skill 脚本使用）──────
# 仅包含 API Key 和端口，不含数据库密码（skill 脚本只需要 API Key 做 HTTP 认证）
cat > "$PREFIX/config.env" << EOF2
MEMORY_API_KEY=${MEMORY_API_KEY}
MEMORY_API_PORT=18793
DEFAULT_DB_PORT=${DB_PORT:-5433}
EOF2
chmod 600 "$PREFIX/config.env"

# ── 生成 systemd service 文件（路径直接写死）─────────────────
echo "配置 systemd 服务..."
cat > /etc/systemd/system/memory-sinovec.service << EOF
[Unit]
Description=SinoVec Memory Layer HTTP API
After=network.target postgresql.service

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=$PREFIX
EnvironmentFile=-/etc/default/sinovec
ExecStart=$PYTHON_CMD $PREFIX/memory_sinovec.py serve --host 127.0.0.1 --port 18793
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable memory-sinovec
systemctl start memory-sinovec

# ── 自动记忆提取与会话索引定时器 ──────────────────────────────
read -p "是否启用自动记忆提取（每小时）和会话索引（每5分钟）？[y/N] " ENABLE_AUTO
if [[ "$ENABLE_AUTO" =~ ^[Yy]$ ]]; then
    echo "配置自动记忆任务（systemd timer）..."

    # 获取 Python 实际路径（支持 venv）
    PYTHON_BIN="$($PYTHON_CMD -c 'import sys; print(sys.executable)' 2>/dev/null || echo "$PYTHON_CMD")"

    # extract service + timer（每小时提取一次）
    cat > /etc/systemd/system/sinovec-extract.service << 'SRVEOF'
[Unit]
Description=SinoVec 自动记忆提取
After=network.target
[Service]
Type=oneshot
ExecStart=REPLACEME
StandardOutput=journal
StandardError=journal
SRVEOF
    # 替换 ExecStart 占位符
    sed -i "s|ExecStart=REPLACEME|ExecStart=$PYTHON_BIN $PREFIX/extract_memories_sinovec.py --scan-recent --hours 1|" \
        /etc/systemd/system/sinovec-extract.service
    if grep -q "REPLACEME" /etc/systemd/system/sinovec-extract.service; then
        echo "⚠️  sinovec-extract.service 模板替换失败，请检查 $PREFIX 路径" >&2
    fi

    cat > /etc/systemd/system/sinovec-extract.timer << 'EOF'
[Unit]
Description=SinoVec 自动记忆提取定时器
[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
Persistent=true
[Install]
WantedBy=timers.target
EOF

    # index service + timer（每5分钟索引会话）
    cat > /etc/systemd/system/sinovec-index.service << 'SRVEOF2'
[Unit]
Description=SinoVec 会话历史索引
After=network.target postgresql.service
[Service]
Type=oneshot
ExecStart=REPLACEME
StandardOutput=journal
StandardError=journal
SRVEOF2
    sed -i "s|ExecStart=REPLACEME|ExecStart=$PYTHON_BIN $PREFIX/session_indexer_sinovec.py index|" \
        /etc/systemd/system/sinovec-index.service
    if grep -q "REPLACEME" /etc/systemd/system/sinovec-index.service; then
        echo "⚠️  sinovec-index.service 模板替换失败，请检查 $PREFIX 路径" >&2
    fi

    cat > /etc/systemd/system/sinovec-index.timer << 'EOF'
[Unit]
Description=SinoVec 会话索引定时器
[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
Persistent=true
[Install]
WantedBy=timers.target
EOF

    systemctl daemon-reload
    systemctl enable --now sinovec-extract.timer sinovec-index.timer

    if systemctl is-active --quiet sinovec-extract.timer && \
       systemctl is-active --quiet sinovec-index.timer; then
        echo "✅ 自动记忆任务已启动"
    else
        echo "⚠️  自动记忆任务启动异常，可手动检查: systemctl status sinovec-*.timer"
    fi
else
    echo "⚠️  跳过自动记忆任务"
    echo "   后期启用: sudo systemctl enable --now sinovec-extract.timer sinovec-index.timer"
fi

# ── 安装 OpenClaw 记忆技能 ─────────────────────────────────
OPENCLAW_SKILLS_DIR="/root/.openclaw/skills"
if [ -d "$OPENCLAW_SKILLS_DIR" ]; then
    echo "检测到 OpenClaw，正在安装记忆技能..."
    mkdir -p "$OPENCLAW_SKILLS_DIR/sinovec-memory"
    cp -r "$CURRENT_DIR/skill/." "$OPENCLAW_SKILLS_DIR/sinovec-memory/"

    # 复制 config.env（包含 API Key，供 skill 脚本使用）到 skill 安装根目录
    # 注意：config.env 不含 DB 密码，仅含 API Key（skill 脚本走 HTTP API 只需 Key）
    if [ -f "$PREFIX/config.env" ]; then
        cp "$PREFIX/config.env" "$OPENCLAW_SKILLS_DIR/sinovec-memory/config.env"
        chmod 600 "$OPENCLAW_SKILLS_DIR/sinovec-memory/config.env"
    fi

    # 生成 skill 专用凭证文件（含 DB 密码，供 CLI fallback 使用）
    # 注意：此文件权限 600，仅 root 可读写
    # 使用 bash heredoc 写入，双引号不解释 $ 和反引号，变量在渲染时展开
    # 修复：原 python3 方式若密码含特殊字符（单/双引号）可能导致语法错误
    # 使用 Python 替代 sed 进行配置文件渲染（避免特殊字符注入风险）
    python3 << PYEOF
import os
config = f"""MEMORY_DB_HOST=127.0.0.1
MEMORY_DB_PORT=$DB_PORT
MEMORY_DB_NAME=$DB_NAME
MEMORY_DB_USER=$DB_USER
MEMORY_DB_PASS=$DB_PASS
"""
with open("$OPENCLAW_SKILLS_DIR/sinovec-memory/skill-credentials.env", "w") as f:
    f.write(config)
os.chmod("$OPENCLAW_SKILLS_DIR/sinovec-memory/skill-credentials.env", 0o600)
PYEOF
    chmod 600 "$OPENCLAW_SKILLS_DIR/sinovec-memory/skill-credentials.env"

    echo "✅ 记忆技能已安装到: $OPENCLAW_SKILLS_DIR/sinovec-memory"
else
    echo "⚠️  未检测到 OpenClaw，跳过技能安装"
    echo "   如已安装 OpenClaw，请手动运行以下命令安装技能:"
    echo "   cp -r $PREFIX/skill ~/.openclaw/skills/sinovec-memory"
fi

# ── 验证 ───────────────────────────────────────────────────
sleep 2
if systemctl is-active --quiet memory-sinovec; then
    echo "✅ 服务启动成功"
else
    echo "⚠️  服务启动异常，请检查: systemctl status memory-sinovec"
fi

echo ""
echo "========================================="
echo "  安装完成!"
echo "========================================="
echo ""
echo "管理命令:"
echo "  sudo systemctl status memory-sinovec   # 查看状态"
echo "  sudo systemctl restart memory-sinovec   # 重启"
echo "  sudo systemctl stop memory-sinovec      # 停止"
echo ""
echo "卸载命令:"
echo "  sudo $PREFIX/uninstall.sh"
echo ""
echo "API 地址:"
echo "  http://127.0.0.1:18793/health        # 健康检查"
echo "  http://127.0.0.1:18793/stats         # 统计信息"
echo ""
echo "配置文件: /etc/default/sinovec"
echo "安装目录: $PREFIX"
echo ""
echo "OpenClaw 技能: ~/.openclaw/skills/sinovec-memory"
echo ""
