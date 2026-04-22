#!/bin/bash
#
# SinoVec 卸载脚本
# 用法: sudo ./uninstall.sh
#

set -e

echo "========================================="
echo "  SinoVec 卸载脚本"
echo "========================================="

if [ "$EUID" -ne 0 ]; then
    echo "错误: 请使用 sudo 或以 root 用户运行"
    exit 1
fi

# ── 读取安装配置（容错：文件不存在时各变量使用安全默认值）─────────
if [ -f /etc/default/sinovec ]; then
    set -a
    source /etc/default/sinovec
    set +a
fi

INSTALL_DIR="${SINOVEC_HOME:-/opt/SinoVec}"
# 修复：install.sh 默认创建的数据库名是 memory，不是 sinovec
DB_NAME_TO_DROP="${MEMORY_DB_NAME:-memory}"

read -p "确认卸载 SinoVec（安装目录: $INSTALL_DIR）？[y/N] " CONFIRM
CONFIRM="${CONFIRM:-N}"
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "已取消"
    exit 0
fi

# ── 停止并禁用所有服务 ─────────────────────────────────────────
echo "停止服务..."
systemctl stop memory-sinovec 2>/dev/null || true
systemctl disable memory-sinovec 2>/dev/null || true
echo "✅ 主服务已停止"

# ── 停止并禁用定时器（安装时可能启用了自动记忆提取和会话索引）─────
echo "停止定时器..."
systemctl stop sinovec-extract.timer 2>/dev/null || true
systemctl disable sinovec-extract.timer 2>/dev/null || true
systemctl stop sinovec-index.timer 2>/dev/null || true
systemctl disable sinovec-index.timer 2>/dev/null || true
echo "✅ 定时器已停止"

# ── 删除所有 systemd unit 文件 ───────────────────────────────
echo "删除 systemd 服务配置..."
rm -f /etc/systemd/system/memory-sinovec.service
rm -f /etc/systemd/system/memory-sinovec.socket  # 若曾安装过 socket 文件则删除
# 删除自动记忆相关 unit（使用 glob 防止重命名后漏删）
rm -f /etc/systemd/system/sinovec-extract.service \
       /etc/systemd/system/sinovec-extract.timer \
       /etc/systemd/system/sinovec-index.service \
       /etc/systemd/system/sinovec-index.timer
# glob 兼容：删除所有 sinovec-* 相关的 service 和 timer
rm -f /etc/systemd/system/sinovec-*.service \
       /etc/systemd/system/sinovec-*.timer \
       /etc/systemd/system/sinovec-*.socket
systemctl daemon-reload
echo "✅ systemd 配置已删除"

# ── 删除 sinovec 服务用户（可选）────────────────────────────
read -p "是否删除 sinovec 系统用户？[y/N] " DEL_SINOVEC_USER
DEL_SINOVEC_USER="${DEL_SINOVEC_USER:-N}"
if [[ "$DEL_SINOVEC_USER" == "y" || "$DEL_SINOVEC_USER" == "Y" ]]; then
    if id "sinovec" &>/dev/null; then
        userdel sinovec 2>/dev/null && echo "✅ sinovec 用户已删除" || echo "⚠️  无法删除 sinovec 用户（可能仍有进程）"
    fi
fi

# ── 删除环境变量配置 ────────────────────────────────────────
echo "删除环境变量配置 /etc/default/sinovec..."
rm -f /etc/default/sinovec
echo "✅ 环境变量配置已删除"

# ── 删除安装目录 ────────────────────────────────────────────
read -p "是否删除安装目录 $INSTALL_DIR？[y/N] " DEL_DIR
DEL_DIR="${DEL_DIR:-N}"
if [[ "$DEL_DIR" == "y" || "$DEL_DIR" == "Y" ]]; then
    echo "删除安装目录..."
    rm -rf "$INSTALL_DIR"
    echo "✅ 安装目录已删除"
else
    echo "跳过删除安装目录（手动清理: rm -rf $INSTALL_DIR）"
fi

# ── 删除数据库（可选）──────────────────────────────────────
# 安全验证：PostgreSQL identifier 必须是小写字母、数字、下划线，且以字母或下划线开头
# 与 install.sh 的验证保持一致，防止通过修改 /etc/default/sinovec 进行注入
if [[ ! "$DB_NAME_TO_DROP" =~ ^[a-z][a-z0-9_]*$ ]]; then
    echo "⚠️  数据库名称格式不安全（$DB_NAME_TO_DROP），跳过删除" >&2
    DEL_DB="N"
fi
read -p "是否删除数据库 $DB_NAME_TO_DROP？[y/N] " DEL_DB
DEL_DB="${DEL_DB:-N}"
if [[ "$DEL_DB" == "y" || "$DEL_DB" == "Y" ]]; then
    echo "删除数据库 $DB_NAME_TO_DROP..."
    # 使用双引号包裹 identifier 是 PostgreSQL 最佳实践，防止特殊字符问题
    sudo -u postgres psql -c "DROP DATABASE IF EXISTS \"$DB_NAME_TO_DROP\";" 2>/dev/null || true
    echo "✅ 数据库 $DB_NAME_TO_DROP 已删除"
fi

# ── 删除 OpenClaw 记忆技能（如果存在）───────────────────────
OPENCLAW_SKILL_DIR="/root/.openclaw/skills/sinovec-memory"
if [ -d "$OPENCLAW_SKILL_DIR" ]; then
    read -p "是否删除 OpenClaw 记忆技能 $OPENCLAW_SKILL_DIR？[y/N] " DEL_SKILL
    DEL_SKILL="${DEL_SKILL:-N}"
    if [[ "$DEL_SKILL" == "y" || "$DEL_SKILL" == "Y" ]]; then
        rm -rf "$OPENCLAW_SKILL_DIR"
        echo "✅ OpenClaw 记忆技能已删除"
    fi
fi

echo ""
echo "========================================="
echo "  卸载完成!"
echo "========================================="
echo ""
echo "如需重新安装，请运行: sudo ./install.sh"
echo ""
