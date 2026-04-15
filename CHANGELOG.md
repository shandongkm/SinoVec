# Changelog

## v1.0.2 (2026-04-15)

### 🔒 Security
- **移除硬编码数据库密码**：现在必须通过 `MEMORY_DB_PASS` 环境变量提供密码，否则服务拒绝启动。三个核心文件均已移除默认密码。
- **添加 HTTP API 认证**：支持 `Authorization: Bearer <key>`、`X-API-Key: <key>`、`?api_key=<key>` 三种方式，通过 `MEMORY_API_KEY` 环境变量启用（v1.0.2+ 推荐生产环境设置）。

### 🐛 Bug Fixes
- **修复连接池线程安全问题**：`_get_pool()` 现在使用 double-checked locking，避免多线程下创建多个连接池。
- **修复向量缓存无限制增长**：改用 `TTLCache`（最多 1000 条，TTL 1 小时），防止长期运行内存溢出。

### ⚡ Improvements
- **数据库连接上下文管理器**：新增 `get_conn()` 上下文管理器，简化资源管理，防止连接泄漏。
- **统一模型初始化路径**：删除冗余的 `_get_fastembed_model` 函数，所有初始化在锁内完成，消除双重初始化风险。

### 📦 Dependencies
- 新增 `cachetools>=5.3.0`

---

## v1.0.1 (2026-04-15)

### 🐛 Bug Fixes
- **fts 生成列冲突**：修复 `extract_memories.py` 和 `session_indexer.py` 手动插入 `fts` 导致 PostgreSQL 报错的问题（`column "fts" is a generated column`）
- **DB_CONFIG 不一致**：修复辅助脚本端口/用户/密码与 `memory_layer.py` 不统一的問題（端口 5432→5433，用户 postgres→openclaw）
- **硬编码代理**：移除 `generate_vector` 中写死的 `HF_HUB_PROXY=http://127.0.0.1:7890`
- **cmd_dedup 硬编码常量**：`< 0.15` 改为 `COSINE_DIST_MERGE`，`> 1` 改为 `DEDUP_WINDOW_HOURS`

### ✨ New Features
- **新增 `/stats` API 端点**：返回 total、recall_total、recall_max、hot_24h 统计信息
- **新增 `--dry-run` 模式**：`extract_memories.py` 和 `session_indexer.py` 支持 dry-run 测试
- **模型全局单例缓存**：辅助脚本新增 `_embedding_model` 全局单例，避免每次调用重复加载模型

### ⚡ Improvements
- **`_ollama_generate` 返回值**：异常时返回空字符串 `""` 而非 `None`，保持类型一致
- **删除重复代码**：移除 `memory_layer.py` 中 `_fastembed_model` 的重复定义

---

## v1.0.0 (2026-04-09)

- 初始版本：SinoVec v1.0
- 向量检索 + BM25 混合搜索
- LLM 查询扩展与重排
- 热度晋升机制（HOT/WARM/COLD）
- 记忆血缘追踪
