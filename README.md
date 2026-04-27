# SinoVec - 高精度中文语义记忆系统

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![pgvector](https://img.shields.io/badge/pgvector-0.5+-green.svg)](https://github.com/pgvector/pgvector)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![版本](https://img.shields.io/badge/version-v1.0.9-blue.svg)](CHANGELOG.md)

📌 **[开发路线图](roadmap.md)** - 了解 SinoVec 的过去、现在和未来计划。

**SinoVec** 是专为中文场景设计的本地化、高精度、零 API 成本的长期记忆系统。基于 pgvector + FastEmbed 实现向量 + BM25 混合检索，可无缝集成到 OpenClaw 等 AI Agent 框架中。

## ⚡ 快速开始

### 方式一：自动安装（推荐 ⭐⭐⭐⭐⭐）

### 把本页面的网址发送给你的OpenClaw，同时指示“阅读并进行安装”。

### 方式二：Docker 一键部署（推荐 ⭐⭐⭐）

#### Gitee用户

```bash
git clone https://gitee.com/confucius-and-mencius/SinoVec.git
cd SinoVec
cp .env.example .env
docker-compose up -d
```

#### GitHub用户

```bash
git clone https://github.com/shandongkm/SinoVec.git
cd SinoVec
cp .env.example .env
docker-compose up -d
```

### 方式三：手动部署（推荐 ⭐）
#### Gitee用户

```bash
git clone https://gitee.com/confucius-and-mencius/SinoVec.git
cd SinoVec

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
#### GitHub 用户
```bash
git clone https://github.com/shandongkm/SinoVec.git
cd SinoVec

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



#### 安装脚本会自动：

*   安装 PostgreSQL + pgvector + zhparser
*   创建数据库和用户
*   配置 systemd 服务
*   **自动检测并安装 OpenClaw 记忆技能**（如已安装 OpenClaw）

## 🤖 LLM 增强（可选）

SinoVec 支持可选的 LLM 增强功能（通过 Ollama 本地推理）：

*   **查询扩展**：将短查询展开为多个相关关键词，提升召回率
*   **结果重排**：用 LLM 对候选结果二次打分，提高相关性

### 三级降级机制
*SinoVec 会自动检测安装本地Ollama qwen2.5模型。
*SinoVec 内置三级降级保障，确保无 LLM 时仍能正常工作：

| 级别  | 条件                              | 行为                     |
| --- | ------------------------------- | ---------------------- |
| 第1级 | Ollama + 主模型（默认 `qwen2.5:7b`）可用 | LLM 扩展 + 重排全开          |
| 第2级 | 主模型不可用，降级到 `qwen2.5:3b`         | LLM 扩展 + 重排全开          |
| 第3级 | Ollama 未安装或所有模型均失败              | **自动降级**，仅使用向量+BM25 检索 |

### 安装 Ollama（install.sh 交互选择）

运行安装脚本时，会询问是否安装 Ollama：

```bash
是否安装 Ollama？[y/N]: y
请选择 LLM 模型：
  1) qwen2.5:7b（精度更高，需约6GB 磁盘空间）
  2) qwen2.5:3b（轻量省资源，需约2GB 磁盘空间）
```

### 手动安装 Ollama

```bash
# 安装 Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 拉取模型
ollama pull qwen2.5:7b   # 或 qwen2.5:3b

# 验证
ollama run qwen2.5:7b "你好"
```

### 环境变量

```bash
OLLAMA_BASE_URL=http://127.0.0.1:11434   # Ollama 服务地址
OLLAMA_MODEL=qwen2.5:7b                  # 主模型
OLLAMA_FALLBACK_MODELS=qwen2.5:3b        # 降级模型（逗号分隔）
```

## 📡 API 接口

### 搜索记忆

```bash
curl -H "X-API-Key: 你的密钥" "http://127.0.0.1:18793/search?q=关键词&top_k=3"
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

**技能触发场景举例：**

*   用户说"记得之前..."等
*   "之前说过什么关于..."等
*   "上次我们说的..."等
*   需要检索历史对话内容

**手动触发（在我回复前加上）：**

    /skill sinovec-memory

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

    SinoVec/
    ├── README.md                    # 项目说明
    ├── roadmap.md                  # 开发路线图
    ├── memory_sinovec.py           # 核心入口（HTTP API 服务 + CLI，兼容旧导入路径）
    ├── sinovec_core/         # 核心代码子包
    │   ├── constants.py            # 配置常量（环境变量统一入口）
    │   ├── db.py                   # 数据库连接池、TS_CONFIG 检测、文件锁
    │   ├── llm.py                  # FastEmbed 向量生成、Ollama LLM、查询扩展、重排
    │   ├── search.py               # 检索核心（向量+BM25 混合检索、MMR 去重）
    │   ├── dedup.py                # 去重（语义+时效/深度向量聚类）、热度晋升
    │   ├── analysis.py             # 召回分析、会话缺口分析、血缘清理
    │   ├── commands.py             # CLI 命令实现
    │   └── http_server.py          # HTTP API 服务器
    ├── extract_memories_sinovec.py # 自动记忆提取脚本
    ├── session_indexer_sinovec.py  # 会话索引脚本
    ├── common.py                   # 公共模块（连接池 + FastEmbed Embedding）
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

*   **生产环境务必设置 `MEMORY_API_KEY`**，否则只有 `/health` 端点可访问，其他 API 请求会返回 401 未授权错误
*   **不要将 API 服务暴露到公网**
*   `MEMORY_DB_PASS` 环境变量必须设置，否则服务拒绝启动
*   数据库密码使用强密码
*   定期备份数据库

## 🔒 安全审查报告

本项目于 2026-04-27 进行了第七轮代码安全审查，审查范围包括：

### ✅ 验证通过项

| 检查项 | 状态 | 说明 |
| --- | --- | --- |
| Python 语法验证 | ✅ | 所有 .py 文件通过 `python3 -m py_compile` |
| Shell 语法验证 | ✅ | 所有 .sh 文件通过 `bash -n` |
| SQL 参数化 | ✅ | 所有数据库查询使用 `%s` 参数化，含 f-string 模板的 `like_conditions` 由 `_escape_like()` 构建后参数化 |
| 凭证管理 | ✅ | 数据库密码从环境变量读取，API Key 文件权限 600 |
| API 认证 | ✅ | 使用 `hmac.compare_digest` 定时安全比较 |
| 时区处理 | ✅ | 统一使用 UTC 时区 (`datetime.now(timezone.utc)`) |
| pgvector 索引 | ✅ | 正确配置 IVFFlat 索引 (vector_cosine_ops)，IVFFlat lists=100 |
| 连接池 | ✅ | SimpleConnectionPool (1-20 连接)，线程安全 |
| 命令注入 | ✅ | 未发现 `eval()`、`os.system()` 或 `subprocess(shell=True)` |
| 文件锁 | ✅ | 使用 `fcntl.flock` 防止并发写入冲突 |
| curl 超时 | ✅ | `install.sh` curl 下载添加 `--max-time 120` |
| ILIKE 转义 | ✅ | 使用全角字符替代 ILIKE 通配符（`%`→`\uff05`，`_`→`\uff3f`） |
| 凭证文件写入安全 | ✅ | `skill-credentials.env` 生成改用 Python `shlex.quote()` 转义 |
| API Key URL 参数传递 | ✅ | 已移除 `?api_key=` 支持，仅支持 `X-API-Key` header |
| HTTP 认证安全强化 | ✅ | `_check_auth()` 空密钥时拒绝非 /health 请求并记录错误日志 |
| 服务运行用户 | ✅ | SERVICE_USER=sinovec，非 root 运行 |
| 目录权限 | ✅ | 安装目录 chown sinovec:sinovec，热记忆文件隔离 |
| 状态文件权限 | ✅ | session_indexer 状态文件 chmod 600/700 |
| systemd 资源限制 | ✅ | LimitNOFILE=1024, MemoryMax=512M |

### 🐛 历史累积问题（均已修复）

| # | 问题 | 文件 | 风险 | 版本 |
|---|------|------|------|------|
| 1 | top_k 参数无范围限制（DoS） | `memory_sinovec.py` | 中 | v1.0.7 |
| 2 | install.sh sed 元字符注入 | `install.sh` | 中 | v1.0.7 |
| 3 | 状态文件泄露会话路径 | `session_indexer_sinovec.py` | 低 | v1.0.7 |
| 4 | extract_memories 时区混用 | `extract_memories_sinovec.py` | 低 | v1.0.7 |
| 5 | _escape_like 缺少单引号转义 | `memory_sinovec.py` | 低 | v1.0.7 |
| 6 | curl 下载无超时（永久挂起） | `install.sh` | 中 | v1.0.7 |
| 7 | ILIKE 通配符无法转义为字面值 | `search.py` | 中 | v1.0.8 |
| 8 | `re.sub("[%s]" % chars)` 语法错误 | `session_indexer_sinovec.py` | 低 | v1.0.8 |
| 9 | 数据库标识符允许数字开头 | `install.sh` | 低 | v1.0.8 |
| 10 | 密码含特殊字符导致 heredoc 解析失败 | `install.sh` | 低 | v1.0.8 |
| 11 | `temporal_decay_score` 异常处理过于宽泛 | `llm.py` | 低 | v1.0.8 |
| 12 | 服务以 root 用户运行 | `install.sh` | 高 | v1.0.8 |
| 13 | API Key 通过 URL 参数传递（日志泄露） | `http_server.py` | 中 | v1.0.8 |
| 14 | 热记忆文件写入 workspace（权限问题） | `dedup.py` | 低 | v1.0.8 |
| 15 | session_indexer 状态文件权限未设置 | `session_indexer_sinovec.py` | 低 | v1.0.8 |

---

**审查结论**: 项目整体安全性良好，历史 15 个安全问题均已在 v1.0.8 中修复，本轮新增 3 个问题（fts列缺失/向量反序列化/API Key管理），均已在 v1.0.9 中修复。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 License

MIT License - 详见 [LICENSE](LICENSE) 文件
