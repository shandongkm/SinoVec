# SinoVec - 中文语义记忆系统

基于 pgvector + FastEmbed 的本地中文语义记忆存储与检索系统。

## 特性

- 🌐 **中文优化** - 使用 BAAI/bge-small-zh-v1.5 embedding 模型
- 🔍 **语义搜索** - 向量检索 + BM25 混合搜索
- 🔄 **自动记忆提取** - 从对话中自动提取值得长期记忆的内容
- 📊 **分层记忆** - HOT/WARM/COLD 三层记忆管理
- 🛠️ **会话索引** - 自动索引对话历史
- ⚡ **快速部署** - 支持 Docker 一键部署

## 架构

```
┌─────────────────────────────────────────┐
│           OpenClaw / 其他 AI           │
│              (客户端)                   │
└──────────────┬──────────────────────────┘
               │ HTTP /search?q=...
               ▼
┌─────────────────────────────────────────┐
│          SinoVec API (port 18793)       │
│         memory_layer.py serve            │
└──────────────┬──────────────────────────┘
               │ SQL
               ▼
┌─────────────────────────────────────────┐
│          PostgreSQL + pgvector           │
│              (向量数据库)                 │
└─────────────────────────────────────────┘
               ▲
               │
┌──────────────┴──────────────────────────┐
│           定时任务 (cron)                │
│  extract_memories.py  自动提取记忆       │
│  session_indexer.py   索引会话历史       │
│  memory_layer.py organize 整理记忆       │
└─────────────────────────────────────────┘
```

## 快速开始

### 方式一：Docker 部署（推荐）

```bash
git clone https://github.com/yourname/sinovec.git
cd sinovec
cp examples/config.env .env
# 编辑 .env 填入你的配置
docker-compose up -d
```

### 方式二：手动部署

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 初始化数据库
psql -U postgres -c "CREATE DATABASE memory;"
psql -U postgres -d memory -f schema.sql

# 3. 配置环境变量
cp examples/config.env .env

# 4. 启动服务
python memory_layer.py serve --host 127.0.0.1 --port 18793
```

## API 接口

### 搜索记忆
```bash
curl "http://127.0.0.1:18793/search?q=关键词&top_k=3&rerank=0&expand=0"
```

### 健康检查
```bash
curl http://127.0.0.1:18793/health
```

### 添加记忆
```bash
python memory_layer.py add "这是一条测试记忆" --user 用户名
```

### 查看统计
```bash
python memory_layer.py stats
```

## 配置说明

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `MEMORY_DB_HOST` | 数据库地址 | 127.0.0.1 |
| `MEMORY_DB_PORT` | 数据库端口 | 5432 |
| `MEMORY_DB_NAME` | 数据库名 | memory |
| `MEMORY_DB_USER` | 数据库用户 | postgres |
| `MEMORY_DB_PASS` | 数据库密码 | (必填) |
| `HF_HUB_PROXY` | HuggingFace 代理 | (可选) |

## 项目结构

```
sinovec/
├── memory_layer.py       # 核心 API 服务
├── extract_memories.py   # 自动记忆提取脚本
├── session_indexer.py    # 会话索引脚本
├── schema.sql            # 数据库表结构
├── requirements.txt     # Python 依赖
├── install.sh           # 安装脚本
├── memory_layer.service # systemd 服务配置
└── examples/
    ├── config.env       # 配置示例
    └── docker-compose.yml
```

## 依赖

- Python 3.10+
- PostgreSQL 14+ with pgvector extension
- FastEmbed (本地向量生成)
- psycopg2 (数据库连接)
- jieba (中文分词)

## License

MIT
