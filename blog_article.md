# SinoVec：为中文 AI Agent 打造的高精度记忆系统

## 引言

在构建 AI Agent 的过程中，"长期记忆"一直是个棘手的问题。Agent 能否记住用户说过的话、做过的约定、偏好习惯，直接决定了对话的连贯性和用户体验。

市面上现有的记忆方案各有局限：

- **OpenClaw 原生文件记忆**：透明但检索能力弱，无法语义匹配
- **Mem0 / Zep 等第三方服务**：便捷但有 API 成本，中文支持一般
- **自建 RAG**：技术门槛高，需要自己维护向量数据库和检索链路

于是我们自研了 **SinoVec** —— 专为中文场景设计的本地化、高精度、零 API 成本的长期记忆系统。

> **项目已开源**
> GitHub：https://github.com/shandongkm/SinoVec
> Gitee：https://gitee.com/confucius-and-mencius/SinoVec

---

## 核心特性

| 特性 | 说明 |
|-------|------|
| **中文深度优化** | jieba 分词 + BAAI/bge-small-zh-v1.5 专用 Embedding（512维） |
| **七层检索增强** | LLM 查询扩展 → 动态权重混合 → 时间衰减 → LLM 重排 → MMR 多样性去重 → 血缘追踪 → 访问热度更新 |
| **零 API 成本** | FastEmbed 本地带量化 + Ollama 本地 LLM，无需任何云端服务 |
| **自动化闭环** | 自动记忆提取 → 存储 → 整理去重 → 主动注入 |
| **血缘追踪** | 每一次合并/删除操作均可回溯 |
| **与 OpenClaw 无缝集成** | 提供 HTTP API 和 custom 插件 |

---

## 系统架构

```
┌──────────────────────────────────────────────────┐
│                    OpenClaw                       │
│               (或其他 AI Agent)                   │
└─────────────────────┬────────────────────────────┘
                      │ HTTP GET /search?q=...&rerank=1&expand=1
                      ▼
┌──────────────────────────────────────────────────┐
│           SinoVec API (端口 18793)               │
│         memory_layer.py serve (完整增强版)         │
└─────────────────────┬────────────────────────────┘
                      │ SQL
                      ▼
┌──────────────────────────────────────────────────┐
│          PostgreSQL + pgvector                    │
│        (mem0 向量表 + memory_lineage 血缘表)      │
└──────────────────────────────────────────────────┘
```

---

## 检索流程：七层增强

SinoVec 的搜索不是一次查询，而是**七层管道**，每层逐步精炼结果。

### 第一层：LLM 查询扩展

当用户输入模糊或简短时，仅靠原始 query 检索会遗漏大量相关内容。SinoVec 会先用本地 LLM（Ollama qwen2.5:7b）生成 3-5 个相关关键词，扩展召回范围。

```python
# 原始查询："用户偏好"
# LLM 扩展后：["用户偏好", "使用习惯", "个人设置", "配置", "口味"]
```

> 扩展结果会被缓存（LRU，128条），5 分钟内相同 query 直接命中缓存，不消耗 LLM 调用。

### 第二层：动态权重混合检索

向量通道和 BM25 通道不是固定权重，而是根据查询特征**动态调整**：

| 查询场景 | 向量权重 | BM25权重 | 触发条件 |
|---------|---------|---------|---------|
| 短查询（<3字） | 85% | 15% | 语义为主 |
| 含专有名词/数字 | 35% | 65% | 精确匹配重要 |
| 两通道重叠度低 | 55% | 45% | 互补性强 |
| 默认场景 | 70% | 30% | — |

```python
# jieba 分词 + pgvector cosine distance + BM25 ts_rank
# 三路并行，结果按动态权重融合评分
```

### 第三层：时间衰减

记忆越新越有价值。SinoVec 对每条结果应用**指数衰减**：

```
decay_score = original_score × 0.5^(age_days / 30)
```

30 天前的记忆衰减约 50%，180 天前衰减约 90%，但不会被彻底丢弃（永远不为零）。

### 第四层：LLM 重排

对混合评分最高的 Top-20 候选，调用 Ollama 二次打分重排：

```
Prompt: "问题：「用户最近的项目进展」
        以下是与问题相关的记忆片段，请对每条打分（0-1分）..."
```

> **降级策略**：候选 ≤5 条时跳过 LLM 调用，直接用混合分数，避免不必要的延迟。

### 第五层：MMR 多样性去重

Maximal Marginal Relevance 算法确保结果**既相关又多样**：

```python
MMR_score = λ × relevance - (1-λ) × max_similarity_to_selected
```

λ=0.5 时，在相关性和多样性之间取得平衡；结果过短或过相似的条目会被剔除。

### 第六层：血缘追踪

每次去重合并或删除操作都会写入 `memory_lineage` 表，可随时追溯：

| 字段 | 说明 |
|------|------|
| `source_id` | 被操作的记忆 ID |
| `operation` | `merge` / `delete` / `extract` |
| `target_id` | 合并到的目标记忆 |
| `reason` | 操作原因（含向量距离/时间差） |
| `created_at` | 操作时间 |

### 第七层：访问热度更新

被命中的记忆批量更新 `access_count` 和 `last_access_time`（一次 SQL 提交，高效），为后续热度晋升提供数据。

---

## 完整检索参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `rerank=0/1` | 禁用/启用 LLM 重排 | 1（启用） |
| `expand=0/1` | 禁用/启用 LLM 查询扩展 | 1（启用） |
| `top_k` | 返回结果数 | 5 |
| `user_id` | 多用户隔离 | None |

---

## 性能数据

| 指标 | 数据 |
|------|------|
| 向量维度 | 512 维 |
| 向量生成速度 | ~250ms/条（FastEmbed 本地推理） |
| 检索延迟（含重排）| 50-150ms（Ollama 调用时间视硬件而定） |
| 当前已存储记忆 | 3,000+ 条 |
| Ollama 并发上限 | 2（信号量保护，防雪崩） |

---

## 定时任务体系

SinoVec 配套多个定时任务，保持记忆库的持续更新：

| 任务 | 调度 | 说明 |
|------|------|------|
| **自动记忆提取** | 每30分钟 | 从最近对话中提取值得长期记忆的内容 |
| **Session 索引** | 00:02 / 12:02 | 将对话历史切片存入记忆库 |
| **每日记忆整理** | 00:10 / 12:10 | 去重合并、HOT→WARM→COLD 流转 |
| **热度晋升** | 每小时 | access_count 高的记忆写入 L2 文件层 |
| **深度去重** | 每周日 | pgvector 向量最近邻聚类去重 |
| **血缘清理** | 每月1日 | 清理 90 天前的血缘记录 |

---

## CLI 工具箱

```bash
# 搜索记忆
python memory_layer.py search "用户偏好" --top-k 5

# 添加记忆
python memory_layer.py add "用户喜欢美式咖啡" --user 主人

# 语义去重（合并 cosine_dist < 0.15 的近似记忆）
python memory_layer.py dedup

# 深度去重（dry-run，查看要删哪些）
python memory_layer.py dedup-deep --threshold 0.1
python memory_layer.py dedup-deep --threshold 0.1 --no-dry-run  # 执行删除

# 分析从未被召回的记忆（僵尸记忆）
python memory_layer.py recall-analysis --limit 50

# 分析 session 中尚未写入记忆库的缺口
python memory_layer.py session-gap --gap-threshold 0.3

# 热度晋升（HOT/WARM/COLD 三层）
python memory_layer.py promote-heat

# 每日整理
python memory_layer.py organize

# 清理血缘表
python memory_layer.py lineage-cleanup --days 90 --no-dry-run

# 启动 HTTP API
python memory_layer.py serve --host 127.0.0.1 --port 18793
```

---

## 快速开始（Docker 一键部署）

```bash
git clone https://github.com/shandongkm/SinoVec.git
cd SinoVec
cp .env.example .env
# 编辑 .env，填入数据库密码

docker-compose up -d
```

等待服务启动后，测试 API：

```bash
# 健康检查
curl http://127.0.0.1:18793/health
# → {"status": "ok"}

# 添加记忆
curl "http://127.0.0.1:18793/add?text=用户喜欢美式咖啡&user=主人"

# 语义搜索（默认启用 LLM 扩展 + 重排）
curl "http://127.0.0.1:18793/search?q=咖啡口味偏好&top_k=3"

# 禁用 LLM 扩展（纯向量+BM25，速度更快）
curl "http://127.0.0.1:18793/search?q=咖啡口味偏好&top_k=3&expand=0"
```

---

## 与 OpenClaw 深度集成

将 SinoVec 作为 OpenClaw 的主动记忆后端，每次对话前自动注入相关记忆。

### 1. 注册 custom 插件

在 `~/.openclaw/openclaw.json` 的 `plugins.entries` 中：

```json
{
  "plugins": {
    "entries": {
      "active-memory": { "enabled": false },
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

### 2. 重启 Gateway

```bash
systemctl restart openclaw-gateway
```

---

## 数据库 Schema

SinoVec 依赖两张表（PostgreSQL + pgvector）：

```sql
-- 记忆向量主表
CREATE TABLE mem0 (
    id              UUID PRIMARY KEY,
    vector          vector(512),           -- BAAI/bge-small-zh-v1.5 生成
    payload        JSONB,                 -- data, user_id, source, created_at 等
    fts             TSVECTOR,             -- BM25 全文检索
    source          TEXT DEFAULT 'memory',
    recall_count    INT DEFAULT 0,
    last_access_time TIMESTAMPTZ,
    access_count    INT DEFAULT 0
);

-- 血缘记录表（追踪每次合并/删除操作）
CREATE TABLE memory_lineage (
    id          SERIAL PRIMARY KEY,
    source_id   UUID NOT NULL,
    operation   TEXT NOT NULL,  -- 'merge' | 'delete' | 'extract'
    reason      TEXT,
    target_id   UUID,
    details     JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 项目结构

```
SinoVec/
├── memory_layer.py       # 核心 API 服务（完整增强版，1885行）
├── extract_memories.py   # 自动记忆提取脚本
├── session_indexer.py    # 会话历史索引器
├── schema.sql           # pgvector 数据库表结构
├── Dockerfile           # 容器镜像
├── docker-compose.yml    # 一键部署
├── install.sh           # 快速安装脚本
├── requirements.txt     # Python 依赖
├── tests/
│   └── test_sinovec.py # 单元测试
└── examples/
    └── docker-compose.yml
```

---

## 开源与社区

SinoVec 采用 **MIT 许可证**，欢迎任何形式的贡献。

- GitHub：https://github.com/shandongkm/SinoVec
- Gitee：https://gitee.com/confucius-and-mencius/SinoVec

如果你在使用中遇到问题，欢迎提交 Issue。

---

## 结语

SinoVec 是我们对中文 AI 记忆系统的一次完整实践。从向量检索到 LLM 增强，从时间衰减到血缘追踪，从热度晋升到 MMR 多样性去重——每一步都有具体实现，而非停留在概念层面。

让 AI 拥有真正的中国记忆。
