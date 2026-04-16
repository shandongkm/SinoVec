# 贡献指南

感谢您对 SinoVec 的关注！欢迎提交代码、文档、问题反馈。

## 如何贡献

### 报告问题

发现 Bug？请在 GitHub Issues 中提交，包含：

- 清晰的标题和描述
- 重现步骤
- 预期行为 vs 实际行为
- 环境信息（Python 版本、PostgreSQL 版本等）

### 代码贡献

1. **Fork** 本仓库
2. 创建特性分支：`git checkout -b feature/your-feature`
3. 编写代码
4. 添加测试
5. 提交：`git commit -m "Add: your feature"`
6. 推送到分支：`git push origin feature/your-feature`
7. 创建 **Pull Request**

### 代码风格

- Python 代码遵循 [PEP 8](https://pep8.org/)
- 公共函数添加 docstring
- 变量命名清晰，避免缩写

## 测试要求

提交前请确保：

```bash
# 运行现有测试
pytest tests/ -v

# 添加新功能的测试
```

## 项目结构

```
SinoVec/
├── memory_sinovec.py          # 核心服务
├── extract_memories_sinovec.py   # 记忆提取
├── session_indexer_sinovec.py   # 会话索引
├── common.py               # 公共模块
├── rebuild_memory_sinovec.sql     # 数据库结构
└── tests/              # 测试文件
```

## 问题解答

如有问题，请在 GitHub Discussions 中提问。

---

感谢每一位贡献者！
