# Changelog

## v1.0.7 (2026-04-19)

### 功能修复
- **自动记忆提取去重修复**：`extract_memories_sinovec.py` 的 `is_recent()` 原使用 `last_access_time`（该字段从不更新，恒为 NULL），导致 6 小时去重窗口完全失效。现改用 `created_at`（INSERT 时自动写入）作为去重判断依据。
- **会话缺口分析修复**：`cmd_session_l1_gap` 原尝试读取不存在的 JSONL 文件和 `session_messages` 表，现改为直接查询数据库中 `source='session'` 的已索引片段。
- **热度流转 CLI 修复**：`cmd_promote_by_heat()` 的返回值结构与 CLI 输出格式不匹配，导致 `promote-heat` 命令输出 KeyError 或全零数据，现已修正。
- **skill 脚本路径推断修复**：`add_memory.sh` 和 `search_memories.sh` 安装后路径推断失败（skill 目录多一层），现已支持多层向上搜索。
- **skill 添加记忆 HTTP 端点**：新增 `POST /memory` HTTP 端点，`add_memory.sh` 优先走 HTTP API（只需 API Key），彻底解决非 root 用户无法添加记忆的问题。
- **source_id 分隔符修复**：`session_indexer_sinovec.py` 的 `source_id` 原用单下划线，与含下划线的 session_id 混用产生解析歧义，现改用双下划线 `__` 分隔。

### 健壮性修复
- **卸载残留清理**：新增 timer 服务（`sinovec-extract.timer`、`sinovec-index.timer`）的停止、禁用和文件删除逻辑。
- **数据库名 fallback**：卸载脚本 fallback 数据库名从错误的 `sinovec` 修正为 `memory`。
- **install.sh sed 替换验证**：timer service 文件的 `ExecStart` 占位符替换后增加验证，失败时给出警告而非静默失效。
- **DEDUP_WINDOW_HOURS 统一**：两模块该常量默认值从 1h/6h 不一致统一为 6h。
- **skill 凭证文件**：安装时生成 `skill-credentials.env`，含 DB 密码供 CLI fallback 使用。

### 配置修复
- **端口默认值统一**：所有示例配置统一使用 5433，与 `install.sh` 和 `common.py` 默认值一致。
- **session 片段索引**：在 `rebuild_memory_sinovec.sql` 中新增 `payload->>'source'` 索引，加速缺口分析查询。

### 文档修复
- **API 文档更新**：`api_schema.md` 新增 `POST /memory` 端点说明，更正添加记忆的 CLI 指引。
- **skill 路径修正**：`SKILL.md` 更新脚本路径（安装后实际路径多了 `skill/` 层）。
- **README 版本 badge**：修正 v1.0.5 → v1.0.6。
- **docker-compose.yml**：新增 Ollama 可选服务（`profiles: [llm]`）、会话目录挂载注释。

---

## v1.0.6 (2026-04-18)

### 功能增强
- **Ollama 三级降级机制**：新增 `_ollama_check_available()` 和 `_ollama_model_exists()`，实现三级降级保障。
  - 第1级：Ollama + qwen2.5:7b
  - 第2级：降级到 qwen2.5:3b
  - 第3级：自动降级，仅使用向量+BM25 检索
- **install.sh 交互式 Ollama 安装**：安装时询问是否安装 Ollama，支持选择模型并自动拉取。

### 安全与健壮性
- **install.sh 安全改进**：curl|sh 改为先下载临时文件再执行，添加失败检测。
- **systemctl user 服务**：失败时给出手动启动指引。
- **ollama pull 错误处理**：拉取失败时提示手动重试。

### 文档更新
- **README 结构优化**：Docker 和手动部署方式各分为 Gitee/GitHub 两个子项。
- **LLM 增强章节**：新增三级降级机制说明、安装 Ollama 指引、环境变量说明。

---

## v1.0.5 (2026-04-18)

### 安全修复
- **移除所有硬编码凭证**：技能脚本（`add_memory.sh`、`search_memories.sh`）不再包含数据库密码、API Key、路径等硬编码值，改从 `/etc/default/sinovec` 环境变量文件读取。
- **移除强制代理配置**：`memory_sinovec.py` 不再强制设置 `HTTP_PROXY/HTTPS_PROXY`，仅当 `HF_HUB_PROXY` 环境变量已设置时生效，适应不同网络环境。
- **安装脚本优化**：移除已失效的 sed 凭证替换指令，技能安装更加健壮。

### 文档更新
- **README 完善**：新增方式三「安装脚本」章节，明确 OpenClaw 技能集成说明，更新项目结构。
- **API 文档修正**：`api_schema.md` 修正参数名 `topK` → `top_k`，移除硬编码 API Key 示例。
- **OpenClaw 技能包**：新增 `skill/` 目录，包含 SinoVec OpenClaw AgentSkill，支持安装时自动部署。

### 项目结构优化
- **移除敏感文件**：删除 `.env` 敏感文件（包含真实密码），避免误提交到版本库。
- **Docker 配置统一**：`docker-compose.yml` 移至根目录，简化 Docker 一键部署流程。

---

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
