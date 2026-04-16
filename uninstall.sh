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

# 读取安装目录（从现有配置中获取）
if [ -f /etc/default/sinovec ]; then
    source /etc/default/sinovec
fi

INSTALL_DIR="${SINOVEC_HOME:-/opt/SinoVec}"

read -p "确认卸载 SinoVec（安装目录: $INSTALL_DIR）？[y/N] " CONFIRM
CONFIRM="${CONFIRM:-N}"
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "已取消"
    exit 0
fi

# ── 停止并禁用服务 ─────────────────────────────────────────
echo "停止服务..."
systemctl stop memory-sinovec 2>/dev/null || true
systemctl disable memory-sinovec 2>/dev/null || true
echo "✅ 服务已停止"

# ── 删除 systemd unit ───────────────────────────────────────
echo "删除 systemd 服务配置..."
rm -f /etc/systemd/system/memory-sinovec.service
systemctl daemon-reload
echo "✅ systemd 配置已删除"

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
read -p "是否删除数据库 memory？[y/N] " DEL_DB
DEL_DB="${DEL_DB:-N}"
if [[ "$DEL_DB" == "y" || "$DEL_DB" == "Y" ]]; then
    echo "删除数据库..."
    sudo -u postgres psql -c "DROP DATABASE IF EXISTS memory;" 2>/dev/null || true
    echo "✅ 数据库已删除"
fi

echo ""
echo "========================================="
echo "  卸载完成!"
echo "========================================="
echo ""
echo "如需重新安装，请运行: sudo ./install.sh"
echo ""
