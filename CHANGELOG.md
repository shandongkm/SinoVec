# Changelog

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
