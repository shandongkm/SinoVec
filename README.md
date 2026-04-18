# SinoVec - 高精度中文语义记忆系统

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![pgvector](https://img.shields.io/badge/pgvector-0.5+-green.svg)](https://github.com/pgvector/pgvector)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

📌 **[开发路线图](roadmap.md)** - 了解 SinoVec 的过去、现在和未来计划。

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
psql -U postgres -d memory -f rebuild_memory_sinovec.sql

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env 填入你的配置

# 4. 启动服务
python memory_sinovec.py serve --host 127.0.0.1 --port 18793
```

### 方式三：安装脚本（推荐 ⭐）

```bash
git clone https://gitee.com/confucius-and-mencius/SinoVec.git
cd SinoVec
chmod +x install.sh
sudo ./install.sh
```

安装脚本会自动：
- 安装 PostgreSQL + pgvector + zhparser
- 创建数据库和用户
- 配置 systemd 服务
- **自动检测并安装 OpenClaw 记忆技能**（如已安装 OpenClaw）

## 📡 API 接口

### 搜索记忆

```bash
curl "http://127.0.0.1:18793/search?q=关键词&top_k=3&api_key=你的密钥"
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

## 🔌 与 OpenClaw 集成

SinoVec 提供两种集成方式：

### 方式一：技能集成（推荐）⭐

安装 SinoVec 后，如果检测到 OpenClaw，会自动安装记忆技能到 `~/.openclaw/skills/sinovec-memory/`。

**技能触发场景：**
- 用户说"记得之前..."
- "之前说过什么关于..."
- 需要检索历史对话内容

**手动触发（在我回复前加上）：**
```
/skill sinovec-memory
```

**或直接使用脚本：**
```bash
# 搜索记忆
~/.openclaw/skills/sinovec-memory/scripts/search_memories.sh "关键词"

# 添加记忆
~/.openclaw/skills/sinovec-memory/scripts/add_memory.sh "记忆内容" "用户ID"
```

### 方式二：HTTP API 集成

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

然后重启 OpenClaw Gateway：
```bash
systemctl restart openclaw-gateway
```

## 📁 项目结构

```
SinoVec/
├── README.md                    # 项目说明
├── roadmap.md                  # 开发路线图
├── memory_sinovec.py           # 核心 API 服务
├── extract_memories_sinovec.py # 自动记忆提取脚本
├── session_indexer_sinovec.py  # 会话索引脚本
├── rebuild_memory_sinovec.sql   # 数据库表结构
├── requirements.txt             # Python 依赖
├── Dockerfile                  # 容器镜像构建
├── docker-compose.yml          # Docker 一键部署
├── install.sh                  # 快速安装脚本（含 OpenClaw 技能安装）
├── uninstall.sh                 # 卸载脚本
├── memory-sinovec.service      # systemd 服务配置
├── CHANGELOG.md               # 版本变更日志
├── CONTRIBUTING.md             # 贡献指南
├── LICENSE                    # MIT 许可证
├── .env.example              # 环境变量配置示例
├── .gitignore                # Git 忽略配置
└── skill/                     # OpenClaw 记忆技能
    ├── SKILL.md               # 技能描述
    ├── scripts/               # 脚本
    │   ├── search_memories.sh  # 搜索记忆
    │   └── add_memory.sh      # 添加记忆
    └── references/
        └── api_schema.md      # API 文档
```

## 🧪 测试

```bash
# 运行单元测试
pytest tests/ -v

# 添加测试记忆
python memory_sinovec.py add "测试内容" --user 用户名

# 查看统计
python memory_sinovec.py stats

# 测试记忆提取（dry-run，不实际写入）
python extract_memories_sinovec.py --scan-recent --dry-run

# 测试会话索引（dry-run，不实际写入）
python session_indexer_sinovec.py index --dry-run
```

## 🛡️ 安全提示

- **生产环境务必设置 `MEMORY_API_KEY`**，否则只有 `/health` 端点可访问，其他 API 请求会返回 401 未授权错误
- **不要将 API 服务暴露到公网**
- `MEMORY_DB_PASS` 环境变量必须设置，否则服务拒绝启动
- 数据库密码使用强密码
- 定期备份数据库

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 License

MIT License - 详见 [LICENSE](LICENSE) 文件
