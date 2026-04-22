# 贡献指南

感谢您对 SinoVec 的关注！欢迎提交代码、文档、问题反馈。

## 提交流程

### 报告问题（Bug / 功能建议）

1. 前往 [GitHub Issues](https://github.com/shandongkm/SinoVec/issues) 或 [Gitee Issues](https://gitee.com/confucius-and-mencius/SinoVec/issues)
2. 使用对应模板（Bug Report / Feature Request）
3. 提交时请附上：
   - 清晰的重现步骤或使用场景描述
   - 环境信息：`python --version`、PostgreSQL 版本、操作系统
   - 如与数据库相关，请附上 `sinovec` 表结构和 pgvector 版本

### 代码贡献

1. **Fork** 本仓库
2. 创建特性分支（基于 `main`）：
   ```bash
   git checkout -b fix/your-fix
   # 或
   git checkout -b feat/your-feature
   ```
3. 编写代码，遵循下方的代码规范
4. 运行语法验证：
   ```bash
   python3 -m py_compile memory_sinovec.py
   python3 -m py_compile extract_memories_sinovec.py
   python3 -m py_compile session_indexer_sinovec.py
   python3 -m py_compile common.py
   python3 -m py_compile sinovec_core/*.py
   bash -n install.sh
   bash -n uninstall.sh
   bash -n init-zhparser.sh
   bash -n fix-zhparser.sh
   ```
5. 提交：`git commit -m "fix: 简短描述"`（中文或英文均可）
6. 推送：`git push origin fix/your-fix`
7. 创建 Pull Request，描述改动内容和关联的 Issue

## 代码规范

### Python

- 遵循 [PEP 8](https://pep8.org/)
- 所有公共函数必须添加 docstring（含参数和返回值说明）
- 变量命名清晰，禁用单字母变量名（循环变量除外）
- SQL 查询必须使用 `%s` 参数化，禁止字符串拼接
- 禁止使用 `eval()`、`os.system()`、`subprocess(shell=True)`
- 数据库连接必须使用上下文管理器（`with get_conn() as conn:`）

### Shell 脚本

- 使用 `set -e` 开启错误终止
- 变量引用必须加引号：`"$VAR"` 而非 `$VAR`
- heredoc 模板使用 `<<EOF`（无变量展开），变量通过 Python 或 targeted sed 写入
- curl/wget 必须设置 `--max-time` 超时

### 数据库变更

- 所有表结构变更通过 SQL migration 文件管理（参考 `rebuild_memory_sinovec.sql`）
- 禁止删除已有列或索引（除非明确标记为向后兼容）
- 新增字段需在文档中同步更新

## 安全要求

SinoVec 核心模块直接读取数据库，所有贡献必须通过以下安全审查：

| 检查项 | 要求 |
|--------|------|
| SQL 查询 | 必须使用 `%s` 参数化，禁止拼接用户输入 |
| 凭证处理 | 密码/API Key 不得硬编码，必须从环境变量读取 |
| 文件权限 | 敏感配置文件（`/etc/default/sinovec`）必须 `chmod 600` |
| 命令执行 | 禁止 `os.system`、`eval()`、`subprocess(shell=True)` |

发现任何安全漏洞请**不要**在公开 Issue 中描述，优先通过私消息联系维护者。

## 测试要求

```bash
# 运行全部测试
pytest tests/ -v

# 单独运行某个模块的语法检查
python3 -m py_compile memory_sinovec.py && echo "memory_sinovec.py OK"
python3 -m py_compile extract_memories_sinovec.py && echo "extract_memories_sinovec.py OK"
python3 -m py_compile session_indexer_sinovec.py && echo "session_indexer_sinovec.py OK"
python3 -m py_compile common.py && echo "common.py OK"
python3 -m py_compile sinovec_core/*.py && echo "sinovec_core/*.py OK"
bash -n install.sh && echo "install.sh OK"
bash -n uninstall.sh && echo "uninstall.sh OK"

# 验证数据库连接（需要 MEMORY_DB_PASS 环境变量）
python3 -c "from common import get_conn; print('DB OK')"
```

## 项目结构

```
SinoVec/
├── memory_sinovec.py              # 核心入口（HTTP API 服务 + CLI，兼容旧导入路径）
├── sinovec_core/            # 核心代码子包
│   ├── __init__.py               # 包入口，导出公开 API
│   ├── constants.py              # 配置常量（环境变量统一入口）
│   ├── db.py                     # 数据库连接池、TS_CONFIG 检测、文件锁
│   ├── llm.py                    # FastEmbed 向量生成、Ollama LLM、查询扩展、重排
│   ├── search.py                 # 检索核心（向量+BM25 混合检索、MMR 去重）
│   ├── dedup.py                  # 去重（语义+时效/深度向量聚类）、热度晋升
│   ├── analysis.py               # 召回分析、会话缺口分析、血缘清理
│   ├── commands.py               # CLI 命令实现
│   └── http_server.py            # HTTP API 服务器
├── extract_memories_sinovec.py  # 自动记忆提取脚本
├── session_indexer_sinovec.py     # 会话历史增量索引器
├── common.py                      # 公共模块（连接池 + FastEmbed Embedding）
├── rebuild_memory_sinovec.sql     # 数据库表结构
├── install.sh                     # 安装脚本（含 systemd timer）
├── uninstall.sh                   # 卸载脚本
├── init-zhparser.sh               # zhparser 中文分词初始化
├── fix-zhparser.sh                # zhparser 修复脚本
├── requirements.txt               # Python 依赖
├── Dockerfile                     # 容器镜像
├── docker-compose.yml             # Docker 一键部署
├── memory-sinovec.service         # systemd 服务配置
├── CHANGELOG.md                  # 版本变更日志
├── roadmap.md                    # 开发路线图
├── README.md                     # 项目说明
├── CONTRIBUTING.md               # 本文件
├── LICENSE                      # MIT 许可证
├── .env.example                  # 环境变量示例
├── .gitignore                    # Git 忽略配置
├── examples/
│   └── config.env                # 配置示例
├── assets/                       # 配图资源
├── tests/                        # 单元测试
│   └── test_sinovec.py
└── skill/                       # OpenClaw 记忆技能
    ├── SKILL.md                  # 技能描述
    ├── scripts/
    │   ├── search_memories.sh    # 搜索记忆
    │   └── add_memory.sh         # 添加记忆
    └── references/
        └── api_schema.md         # API 文档
```

## 版本管理

- 采用语义化版本：`v主版本.次版本.修订号`
  - 修订号：Bug 修复、文档更新、安全补丁
  - 次版本：新功能向后兼容
  - 主版本：破坏性变更
- 所有正式发布对应一个 Git tag（格式：`v1.0.8`）
- `CHANGELOG.md` 和 `roadmap.md` 随每个正式版本同步更新

## 审查制度

SinoVec 采用滚动安全审查制度（每轮审查记录在 README.md 安全章节）：

| 轮次 | 日期 | 主要发现 |
|------|------|---------|
| Round 1 | v1.0.2 | 硬编码密码移除、API 认证、TTLCache、线程安全连接池 |
| Round 2 | v1.0.3 | LIKE 注入防护、hmac 定时安全比较、连接泄漏修复 |
| Round 3 | v1.0.5~v1.0.6 | sed 元字符注入、状态文件路径泄露、时区混用等 |
| Round 4 | v1.0.7 | curl 超时缺失、DB_CONFIG 重复池、内容提取正则修复 |
| Round 5 | v1.0.8 | ILIKE通配符转义、标识符校验强化、凭证写入安全、重构为子包、权限治理（root→sinovec）、API Key URL参数移除、状态文件保护、systemd资源限制 |
| Round 6 | v1.0.8 | HTTP 认证安全强化（空密钥时拒绝非/health请求+错误日志）、移除冗余auth检查 |


提交 PR 前请自行检查以上安全项，降低审查轮次成本。

## 问题解答

- **一般问题**：[GitHub Discussions](https://github.com/shandongkm/SinoVec/discussions) / [Gitee](https://gitee.com/confucius-and-mencius/SinoVec/issues)
- **安全问题**：请私消息联系维护者，勿在公开渠道描述

---

感谢每一位贡献者！
