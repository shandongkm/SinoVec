# Changelog

## v1.0.4 (2026-04-16)

### 连接管理回归修复
- **`cmd_search`**：修复 `with` 块提前结束导致的连接提前归还问题，将数据库操作移入 `with get_conn()` 块内的 `try-finally` 中。
- **`cmd_recall_analysis`**：修复 `with` 块作用域错误（连接提前归还）和缺少 `finally` 导致的连接泄漏，添加正确的 `try-finally` 并缩进到 `with` 块内。
- **`cmd_dedup_deep`**：修复 `_preview_clusters` 在 `cur` 和 `conn` 已关闭后仍被调用的 use-after-close 问题，将其移入 `try` 块内。
- **`cmd_lineage_cleanup`**：修复 `try` 块在 `with` 块外导致的连接失效问题，将整个逻辑缩进到 `with` 块内。

### 新增兼容性
- **`cmd_promote_by_heat`**：添加 wrapper 函数，调用 `cmd_organize`，保持 CLI 命令 `promote-heat` 的向后兼容性（原函数已重构）。

### 代码清理
- **`cmd_dedup`**：为内层 `cur2` 添加 `try-finally`，显式关闭游标（代码规范性提升）。
- **`_ollama_safe_call`**：删除 `return None` 后的冗余 `pass`。
- **`/stats` HTTP handler**：删除冗余的 `cur.close()` 调用。

### 验证
- 所有 CLI 命令（`stats`, `list`, `add`, `search`, `recall-analysis`, `lineage-cleanup`, `dedup-deep` 等）功能正常。
- 长时间压力测试确认连接池无泄漏。

---

## v1.0.3 (2026-04-15)

### 安全增强
- **LIKE 通配符注入修复**：`_bm25_search` 中对 jieba_terms 的 `%`、`_`、`\` 进行转义，防止用户输入触发意外模式匹配。
- **API 认证优化**：密钥比较使用恒定时间算法，进一步提升安全性。

### 问题修正
- **连接泄漏修复**：`extract_memories.py` 和 `session_indexer.py` 的数据库操作改用 `contextmanager` 封装，确保异常时连接正确关闭。

### 性能与可靠性
- **统一 Ollama 调用为 requests 库**，错误处理更一致，超时控制更可靠。

---

## v1.0.2 (2026-04-15)

### 安全加固
- **密码管理优化**：数据库密码强制通过环境变量提供，不再包含任何默认值，提升部署安全性。
- **API 访问控制**：新增 `MEMORY_API_KEY` 环境变量，支持 Bearer Token、Header 和 URL 参数三种认证方式；未配置时仅 `/health` 端点可访问，保护敏感数据。

### 智能缓存
- **向量缓存升级为带 TTL 的 LRU 缓存**（最多 1000 条，1 小时过期），内存占用更可控，长期运行更稳定。

### 开发体验
- **连接池线程安全优化**，使用双重检查锁确保高并发下的稳定性。
- **新增 `get_conn()` 上下文管理器**，统一数据库连接获取与释放，代码更简洁。

---

## v1.0.1 (2026-04-15)

### 问题修正
- **fts 生成列冲突**：修复 `extract_memories.py` 和 `session_indexer.py` 手动插入 `fts` 导致 PostgreSQL 报错的问题（`column "fts" is a generated column`）。
- **DB_CONFIG 不一致**：修复辅助脚本端口/用户/密码与主服务保持一致（端口 5433，用户 openclaw），避免连接失败。
- **硬编码代理**：移除 `generate_vector` 中的硬编码代理地址，改为通过环境变量 `HF_HUB_PROXY` 配置，适应不同网络环境。
- **去重命令硬编码阈值**替换为可配置常量，便于生产调优。

### 新功能
- **新增 `/stats` API 端点**，可快速获取记忆总量、召回次数、24 小时活跃记忆等统计信息。
- **自动记忆提取和会话索引脚本支持 `--dry-run` 模式**，方便测试提取效果而不实际写入数据库。
- **辅助脚本引入全局模型单例**，避免重复加载 FastEmbed 模型，加速批量处理。

### 部署优化
- **安装脚本支持虚拟环境和备份配置**，systemd 服务动态生成，提升部署灵活性。

---

## v1.0.0 (2026-04-09)

### 初始发布
- 核心检索：向量 + BM25 混合检索，动态权重调整，支持 LLM 查询扩展与重排。
- 记忆管理：热度晋升（HOT/WARM/COLD）、语义去重、血缘追踪、自动记忆提取、会话索引。
- 部署方式：Docker 一键部署、systemd 服务、一键安装脚本。
- API 服务：提供 `/search`、`/health` 等 HTTP 接口，方便集成到 OpenClaw 等 AI Agent 框架。

SinoVec 致力于为中文 AI Agent 提供本地化、高精度、零 API 成本的长期记忆能力。感谢每一位用户和贡献者的支持！
