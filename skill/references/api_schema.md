# SinoVec API Schema

## 基础信息

- **Base URL**: `http://127.0.0.1:18793`
- **认证**: `X-API-Key` header 或 `?api_key=` 查询参数
- **认证密钥**: 见 `/etc/default/sinovec` 中的 `MEMORY_API_KEY`

## 端点

### GET /search

语义搜索记忆。

**参数**:
- `q` (必填): 查询文本
- `top_k` (可选): 返回数量，默认 3
- `user_id` (可选): 按用户过滤
- `api_key` (必填): 认证密钥

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

### POST /add

添加记忆（通过 CLI 调用，非 HTTP）。

### GET /health

健康检查，无需认证。

**响应**: `{"status": "ok"}`

## 搜索结果处理

- `score` 越高相关性越强
- 空结果返回 `{"count": 0, "results": []}`
- 错误返回 `{"error": "错误信息"}`
