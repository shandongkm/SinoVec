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
echo "Python 版本: $($PYTHON_CMD --version 2>&1 | awk '{print $2}')"

# ── 检查 PostgreSQL ─────────────────────────────────────────
if ! command -v psql &> /dev/null; then
    echo "错误: 未安装 PostgreSQL"
    echo "Ubuntu/Debian: sudo apt install postgresql"
    echo "CentOS/RHEL:   sudo yum install postgresql-server"
    exit 1
fi
echo "PostgreSQL 版本: $(psql --version | awk '{print $3}')"

# ── 检查 pgvector ────────────────────────────────────────────
if psql -U postgres -c "SELECT * FROM pg_extension WHERE extname='vector';" 2>/dev/null | grep -q vector; then
    echo "✅ pgvector 扩展已安装"
else
    echo "⚠️  pgvector 扩展未安装，正在安装..."
    apt update
    apt install -y postgresql-16-pgvector
    sudo -u postgres psql -c "CREATE EXTENSION IF NOT EXISTS vector;"
fi

# ── 数据库配置（默认值与 memory_sinovec.py 一致）─────────────
read -p "数据库端口 [$DEFAULT_DB_PORT]: " DB_PORT
DB_PORT=${DB_PORT:-$DEFAULT_DB_PORT}

read -p "数据库用户 [$DEFAULT_DB_USER]: " DB_USER
DB_USER=${DB_USER:-$DEFAULT_DB_USER}

read -sp "数据库密码: " DB_PASS
echo ""

if [ -z "$DB_PASS" ]; then
    echo "错误: 密码不能为空"
    exit 1
fi

read -p "数据库名称 [memory]: " DB_NAME
DB_NAME=${DB_NAME:-memory}

# ── 创建数据库和用户 ─────────────────────────────────────────
echo "配置数据库..."

sudo -u postgres psql -c "CREATE DATABASE $DB_NAME;" 2>/dev/null || echo "数据库 $DB_NAME 已存在"

if sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
    echo "用户 $DB_USER 已存在"
else
    sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';"
fi
sudo -u postgres psql -c "ALTER USER $DB_USER WITH SUPERUSER;"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"

# ── 导入表结构 ──────────────────────────────────────────────
echo "导入数据库表结构..."
PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" -f "$CURRENT_DIR/rebuild_memory_sinovec.sql"
echo "✅ 表结构已创建"

# ── 安装 Python 依赖 ────────────────────────────────────────
echo "安装 Python 依赖..."
if [ "$USE_VENV" = true ]; then
    $PIP_CMD install -r "$CURRENT_DIR/requirements.txt"
else
    $PIP_CMD install -r "$CURRENT_DIR/requirements.txt" --break-system-packages 2>/dev/null \
        || $PIP_CMD install -r "$CURRENT_DIR/requirements.txt"
fi

# ── 复制代码到安装目录 ─────────────────────────────────────
echo "安装代码到 $PREFIX..."
mkdir -p "$PREFIX"
cp -r "$CURRENT_DIR"/. "$PREFIX"/

# ── 备份已有配置 ───────────────────────────────────────────
if [ -f /etc/default/sinovec ]; then
    echo "备份已有配置: /etc/default/sinovec.bak"
    cp /etc/default/sinovec /etc/default/sinovec.bak
fi

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
EOF

# ── 生成 systemd service 文件（路径直接写死）─────────────────
echo "配置 systemd 服务..."
cat > /etc/systemd/system/memory-sinovec.service << EOF
[Unit]
Description=SinoVec Memory Layer HTTP API
After=network.target postgresql.service

[Service]
Type=simple
User=root
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
