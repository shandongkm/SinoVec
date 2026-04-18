---
name: sinovec-memory
description: 中文语义记忆系统集成。当需要查询长期记忆、搜索过往对话内容、查找相关历史信息、或用户提到"记得之前"、"之前说过"等场景时触发。SinoVec 提供向量检索+全文检索的混合搜索能力。
---

# SinoVec 记忆技能

SinoVec 是基于 pgvector + zhparser 构建的中文语义记忆系统，支持向量相似度和关键词混合检索。

## 快速查询

调用 `scripts/search_memories.sh` 进行记忆搜索：

```bash
cd /root/.openclaw/skills/sinovec-memory
./scripts/search_memories.sh "用户询问的关键词"
```

结果以 JSON 格式返回，包含记忆内容和相关性评分。

## API 端点

- **搜索**：`POST /search` — 语义+全文混合检索
- **统计**：`GET /stats` — 记忆总数、召回统计
- **健康**：`GET /health` — 服务状态

## 认证

使用 `X-API-Key` Header 或 `?api_key=` 查询参数。

API Key 存储在 `/root/SinoVec/.env` 的 `MEMORY_API_KEY`。
API 地址：`http://127.0.0.1:18793`

## 触发场景

- 用户问"之前说过什么"
- 需要引用历史对话内容
- 检索相关记忆上下文
- "你记得..."类型的问题

## 搜索结果处理

搜索结果为 JSON 数组，按相关性评分排序。返回格式：
```json
{
  "count": 2,
  "results": [
    {"id": "...", "data": "记忆内容", "score": 0.85, "source": "memory"}
  ]
}
```

详细内容见 `references/api_schema.md`。
