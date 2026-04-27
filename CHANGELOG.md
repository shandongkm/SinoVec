# Changelog
## v1.0.9 (2026-04-27) - zhparser 中文分词完整支持与向量检索修复

### 功能修复
- **zhparser 完整安装流程**：新增 SCWS 编译安装步骤（从源码编译），解决 `postgresql-server-dev-*` 缺失导致的 `postgres.h` 头文件找不到的问题，完成 zhparser 扩展注册和 `chinese_zh` 文本搜索配置创建
- **fts 生成列初始化**：为已有 `sinovec` 表补充 `fts tsvector` 生成列（使用 `chinese_zh` 分词配置），解决新装 zhparser 后表结构不完整的问题
- **_batch_fetch_vectors 向量反序列化修复**：`pgvector` Python 包未安装时，向量通过 psycopg2 返回字符串类型而非 `numpy.array`，导致 MMR 去重时 `TypeError: can't multiply sequence by non-int of type 'str'`。现增加字符串 JSON 反序列化路径
- **pgvector Python 包依赖**：在 `requirements.txt` 中显式声明 `pgvector` 依赖，确保向量列通过 `pgvector.vec.Vector` 返回可迭代对象

### 安装体验优化
- **zhparser 安装文档完善**：更新 `fix-zhparser.sh` 和 `init-zhparser.sh` 安装说明，明确 SCWS 源码编译步骤和动态 PostgreSQL 版本检测

---

## v1.0.8 (2026-04-20) - 安全加固与权限治理

### 安全加固
- **服务运行用户**：从 root 改为专用账户 sinovec，防止代码执行漏洞提升为系统 root 权限
- **PostgreSQL 认证调整**：安装脚本自动将 peer 认证改为 md5，允许 sinovec 用户通过密码连接数据库
- **目录权限收紧**：安装目录 chown sinovec:sinovec，敏感文件保持 600
- **热记忆路径隔离**：热记忆文件从 `~/.openclaw/workspace/memory/` 迁移到 `$PREFIX/memory/`，解决跨用户权限问题
- **API Key 安全**：移除 URL 参数传递支持（`?api_key=`），仅支持 `X-API-Key` header，防止日志泄露
- **状态文件保护**：session_indexer 状态文件写入时 chmod 600，目录 chmod 700
- **systemd 资源限制**：新增 memory-sinovec.service 的 LimitNOFILE=1024、MemoryMax=512M
- **systemd socket 文件**：新增 memory-sinovec.socket，限制 MaxConnections=20
- **CLI fallback 凭证路径**：/etc/default/sinovec 改为 chmod 640、chown root:sinovec，sinovec 用户可读
- **HTTP 认证安全强化**：`_check_auth()` 在 API Key 未配置时明确拒绝非 /health 请求并记录错误日志；移除 /stats 和 /metrics 端点冗余 auth 检查

### 功能修复
- **HTTP 认证方法文档**：更新 docstring 说明，移除 URL 参数认证相关描述

### 代码维护
- **新增文件**：`sinovec_core/` 子包（db, llm, search, dedup, analysis, commands, http_server, constants）
- **新增文件**：`memory-sinovec.socket` systemd socket 单元文件
- **删除文件**：`memory_sinovec_pkg/` 旧子包目录（已被 sinovec_core 替代）

---

## v1.0.7 (2026-04-19) - 补充修复

### 功能修复
- **HTTP /stats 与 CLI stats 一致性修复**：`/stats` HTTP 端点原使用 `WHERE source = 'memory'` 过滤，仅统计 CLI 添加的记忆，遗漏自动提取和会话索引的记忆。现移除该过滤条件，与 CLI `stats` 命令统计口径统一（统计所有记忆）。
- **时间衰减函数时区修复**：`temporal_decay_score()` 混用 UTC 创建时间与本地时区 `datetime.now()`，当时区非 UTC 时会计算错误。现统一使用 UTC 时间计算，消除时差影响。

### 文档修复
- **README Python 版本要求 badge 修正**：badge 错误标注 "Python 3.10+"，实际代码要求 "Python 3.9+"（`datetime.fromisoformat` timezone 支持从 3.9 开始），已修正。

---

## v1.0.7 (2026-04-19)

### 安全修复
- **skill-credentials.env 生成安全修复**：`install.sh` 原使用 `sed` 替换 `${VAR}`，密码含特殊字符时可能处理异常；现改用 bash heredoc 模板 + targeted sed，规避 shell 注入风险。
- **search_memories.sh 查询参数引用修复**：修复 `$QUERY` 未加引号导致多词查询被截断的问题（`python3 -c` 的 `sys.argv[1:]` 切割），改用 `join` 合并所有参数后统一编码。
- **add_memory.sh CLI 回退凭证加载**：HTTP 回退到 CLI 模式时，新增优先查找 `skill-credentials.env`（OpenClaw 技能安装时生成），确保非 root 用户 CLI fallback 也能获取 DB 密码。
- **session_indexer_sinovec.py 异常处理修复**：`_load_state()` 和 `_get_last_line_hash()` 的裸 `except:` 改为具体异常类型（`FileNotFoundError`, `JSONDecodeError`, `PermissionError`, `OSError`, `IOError`, `IndexError`, `UnicodeDecodeError`），避免掩盖编程错误。

### 功能修复
- **SKILL.md 脚本路径修正**：文档示例路径从错误的 `skill/scripts/` 修正为实际的 `scripts/`（`cp -r skill/.` 会打平目录结构）。
- **Dockerfile 补充缺失脚本**：补充 `init-zhparser.sh` 到 Docker 镜像（原来只有 `fix-zhparser.sh`），确保容器内可执行 zhparser 初始化逻辑。
- **docker-compose.yml 卷挂载路径修复**：`~/.cache/fastembed` 改为 `${HOME:-/root}/.cache/fastembed`，避免 tilde 在 docker-compose volume 映射中某些环境下不展开的问题。
- **extract_from_text 内容提取扩展**：原实现仅捕获 `- *` 列表、`#` 标题和单行代码块，覆盖面极窄；现扩展支持：`[-*○]` 符号列表、`1.` 数字编号、`①` 圈号、` ``` ` 代码块、`key=value` 配置语句、引号内容、含决策词的完整句子等。
- **cmd_dedup 向量邻居上限提升**：最近邻查询从 LIMIT 5 提升到 LIMIT 20，减少遗漏真正重复记忆的概率。

### 功能修复
- **自动记忆提取去重修复：`extract_memories_sinovec.py` 的 `is_recent()` 原使用 `last_access_time`（该字段从不更新，恒为 NULL），导致 6 小时去重窗口完全失效。现改用 `created_at`（INSERT 时自动写入）作为去重判断依据。
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
