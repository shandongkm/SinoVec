#!/bin/bash
# SinoVec 安装脚本

set -e

echo "========================================="
echo "  SinoVec 安装脚本"
echo "========================================="

# 检查 Python 版本
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "Python 版本: $PYTHON_VERSION"

# 检查 PostgreSQL
if command -v psql &> /dev/null; then
    PG_VERSION=$(psql --version | awk '{print $3}')
    echo "PostgreSQL 版本: $PG_VERSION"
else
    echo "错误: 未安装 PostgreSQL"
    echo "Ubuntu/Debian: sudo apt install postgresql"
    echo "CentOS/RHEL: sudo yum install postgresql-server"
    exit 1
fi

# 检查 pgvector 扩展
if psql -U postgres -c "SELECT * FROM pg_extension WHERE extname='vector';" 2>/dev/null | grep -q vector; then
    echo "✅ pgvector 扩展已安装"
else
    echo "⚠️  pgvector 扩展未安装，正在安装..."
    sudo apt update
    sudo apt install -y postgresql-16-pgvector  # 根据你的 PostgreSQL 版本调整
    sudo -u postgres psql -c "CREATE EXTENSION IF NOT EXISTS vector;"
fi

# 创建数据库
echo "请设置数据库配置:"
read -p "数据库用户 [postgres]: " DB_USER
DB_USER=${DB_USER:-postgres}
read -p "数据库名称 [memory]: " DB_NAME
DB_NAME=${DB_NAME:-memory}
read -sp "数据库密码: " DB_PASS
echo ""

# 创建数据库
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME;" 2>/dev/null || echo "数据库已存在"
sudo -u postgres psql -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';"

# 导入表结构
PGPASSWORD=$DB_PASS psql -U $DB_USER -d $DB_NAME -f schema.sql
echo "✅ 数据库表结构已创建"

# 安装 Python 依赖
echo "安装 Python 依赖..."
pip3 install -r requirements.txt

# 配置环境变量
echo "创建配置文件..."
cat > config.env << EOF
# SinoVec 环境配置
export MEMORY_DB_HOST=127.0.0.1
export MEMORY_DB_PORT=5432
export MEMORY_DB_NAME=$DB_NAME
export MEMORY_DB_USER=$DB_USER
export MEMORY_DB_PASS=$DB_PASS

# 可选: HuggingFace 代理 (国内需要)
# export HF_HUB_PROXY=http://127.0.0.1:7890
EOF

# 复制服务配置
sudo cp memory_layer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable memory-layer

echo ""
echo "========================================="
echo "  安装完成!"
echo "========================================="
echo ""
echo "启动服务:"
echo "  sudo systemctl start memory-layer"
echo "  sudo systemctl status memory-layer"
echo ""
echo "测试搜索:"
echo "  curl 'http://127.0.0.1:18793/search?q=测试&top_k=3'"
echo ""
