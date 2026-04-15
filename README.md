# SinoVec - 高精度中文语义记忆系统

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![pgvector](https://img.shields.io/badge/pgvector-0.5+-green.svg)](https://github.com/pgvector/pgvector)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**SinoVec** 是专为中文场景设计的本地化、高精度、零 API 成本的长期记忆系统。基于 pgvector + FastEmbed 实现向量 + BM25 混合检索，可无缝集成到 OpenClaw 等 AI Agent 框架中。

## ⚡ 快速开始

### 方式一：Docker 一键部署（推荐）

```bash
git clone https://github.com/shandongkm/SinoVec.git
cd SinoVec
cp .env.example .env
# 编辑 .env，填入数据库密码
docker-compose up -d
```

### 方式二：手动部署

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 初始化数据库（PostgreSQL 14+ required）
psql -U postgres -c "CREATE DATABASE memory;"
psql -U postgres -d memory -f schema.sql

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env 填入你的配置

# 4. 启动服务
python memory_layer.py serve --host 127.0.0.1 --port 18793
```

## 📡 API 接口

### 搜索记忆

```bash
curl "http://127.0.0.1:18793/search?q=关键词&top_k=3"
```

**响应示例：**
```json
{
  "count": 2,
  "results": [
    {"id": "xxx-xxx", "score": 0.85, "data": "记忆内容..."},
    {"id": "yyy-yyy", "score": 0.72, "data": "另一条记忆..."}
  ]
}
```

### 健康检查

```bash
curl http://127.0.0.1:18793/health
# {"status": "ok"}
```

### 统计信息

```bash
curl http://127.0.0.1:18793/stats
# {"total": 3030, "recall_total": 241, "recall_max": 15, "hot_24h": 46}
```

## 🔧 配置说明

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `MEMORY_DB_HOST` | 数据库地址 | 127.0.0.1 |
| `MEMORY_DB_PORT` | 数据库端口 | 5433 |
| `MEMORY_DB_NAME` | 数据库名 | memory |
| `MEMORY_DB_USER` | 数据库用户 | openclaw |
| `MEMORY_DB_PASS` | 数据库密码 | (必填) |
| `HF_HUB_PROXY` | HuggingFace 代理 | (可选) |

## 🔌 与 OpenClaw 集成

将 SinoVec 作为 OpenClaw 的主动记忆插件使用：

### 1. 注册插件

在 `~/.openclaw/openclaw.json` 的 `plugins.entries` 中添加：

```json
{
  "plugins": {
    "entries": {
      "active-memory-custom": {
        "enabled": true,
        "config": {
          "apiUrl": "http://127.0.0.1:18793/search",
          "topK": 3
        }
      }
    }
  }
}
```

### 2. 启动 SinoVec 服务

```bash
systemctl enable --now memory-layer
```

### 3. 重启 OpenClaw Gateway

```bash
systemctl restart openclaw-gateway
```

## 📁 项目结构

```
SinoVec/
├── memory_layer.py       # 核心 API 服务
├── extract_memories.py   # 自动记忆提取脚本（支持 --dry-run）
├── session_indexer.py     # 会话索引脚本（支持 --dry-run）
├── schema.sql            # 数据库表结构
├── requirements.txt      # Python 依赖
├── Dockerfile            # 容器镜像构建
├── docker-compose.yml    # Docker 一键部署
├── install.sh            # 快速安装脚本
├── CHANGELOG.md          # 版本变更日志
├── CONTRIBUTING.md       # 贡献指南
├── LICENSE               # MIT 许可证
├── .env.example          # 环境变量配置示例
├── .gitignore            # Git 忽略配置
└── examples/
    └── docker-compose.yml
```

## 🧪 测试

```bash
# 运行单元测试
pytest tests/ -v

# 添加测试记忆
python memory_layer.py add "测试内容" --user 用户名

# 查看统计
python memory_layer.py stats

# 测试记忆提取（dry-run，不实际写入）
python extract_memories.py --scan-recent --dry-run

# 测试会话索引（dry-run，不实际写入）
python session_indexer.py index --dry-run
```

## 🛡️ 安全提示

- **不要将 API 服务暴露到公网**
- 数据库密码使用强密码
- 定期备份数据库

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 License

MIT License - 详见 [LICENSE](LICENSE) 文件
