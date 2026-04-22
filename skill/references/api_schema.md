# SinoVec API Schema

## 基础信息

- **Base URL**: `http://127.0.0.1:18793`
- **认证**: `X-API-Key` Header 认证
- **认证密钥**: 见 `/etc/default/sinovec` 中的 `MEMORY_API_KEY`

## 端点

### GET /search

语义搜索记忆。

**参数**:
- `q` (必填): 查询文本
- `top_k` (可选): 返回数量，默认 3
- `user_id` (可选): 按用户过滤
- `rerank` (可选): 是否使用 LLM 重排，默认 1
- `expand` (可选): 是否使用 LLM 查询扩展，默认 1

**响应**:
```json
{
  "count": 2,
  "results": [
    {
      "id": "uuid",
      "data": "记忆内容文本",
      "payload": {"user_id": "...", "data": "..."},
      "score": 0.85,
      "rerank": 0.92,
      "source": "memory",
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

### GET /stats

记忆统计。

**响应**:
```json
{
  "total": 100,
  "recall_total": 50,
  "recall_max": 5,
  "hot_24h": 3
}
```

### GET /health

健康检查，无需认证。

**响应**: `{"status": "ok"}`

### POST /memory（添加记忆）

通过 HTTP API 添加记忆（需要认证）。

**请求体**:
```json
{
  "text": "记忆内容",
  "user_id": "用户名"
}
```

**响应**:
- `201 Created`: `{"id": "uuid", "status": "added"}`
- `409 Conflict`: 内容重复或质量门拒绝
- `400 Bad Request`: 参数缺失或内容为空
- `401 Unauthorized`: API Key 无效

```bash
curl -X POST "http://127.0.0.1:18793/memory" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{"text": "记忆内容", "user_id": "用户名"}'
```

## 添加记忆（CLI）

除 HTTP API 外，也可通过 CLI 添加：

```bash
# 添加单条记忆
python memory_sinovec.py add "记忆内容" --user 用户名

# 强制添加（绕过质量门）
python memory_sinovec.py add "短内容" --user 用户名 --force
```

## 搜索结果处理

- `score` 越高相关性越强
- `rerank` 为 LLM 重排分数（未使用 LLM 时为 null）
- 空结果返回 `{"count": 0, "results": []}`
- 错误返回 `{"error": "错误信息"}`
