# ── SinoVec CLI 命令模块 ────────────────────────────────────────────
"""
CLI 命令实现：
  - add: 添加记忆
  - search: 搜索记忆（CLI 模式）
  - stats: 统计
  - delete: 删除
  - list: 列出记忆
  - summarize: 摘要
  - dedup / dedup-deep: 去重
  - recall-analysis: 召回分析
  - session-gap: 会话缺口分析
  - promote-heat: 热度晋升
  - organize: 每日整理
  - lineage-cleanup: 血缘清理
  - serve: 启动 HTTP 服务
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sinovec_core.db import get_conn
from sinovec_core.llm import generate_vector, compute_hash, _log_lineage
from sinovec_core.search import cmd_search
from sinovec_core.dedup import (
    cmd_dedup,
    cmd_dedup_deep,
    cmd_promote_by_heat,
    cmd_organize,
)
from sinovec_core.analysis import (
    cmd_recall_analysis,
    cmd_session_l1_gap,
    cmd_lineage_cleanup,
)
from sinovec_core.constants import (
    MIN_CONTENT_CHARS,
    COSINE_DIST_DEEP,
    COSINE_DIST_SESS_GAP,
    OVERLAP_LO,
    RECALL_ANALYSIS_LIMIT,
    LINEAGE_CLEANUP_DAYS,
)

logger = logging.getLogger(__name__)


# ── 核心命令 ──────────────────────────────────────────────────────

def cmd_add(text: str, user: str = "主人", force: bool = False) -> str:
    """
    添加记忆。
    force=True: 绕过质量门（短内容也允许添加）
    """
    if not force and len(text) < MIN_CONTENT_CHARS:
        raise ValueError(
            f"内容过短（{len(text)} < {MIN_CONTENT_CHARS}字符），使用 --force 强制添加"
        )

    content_hash = compute_hash(text)

    try:
        vec = generate_vector(text)
    except RuntimeError:
        vec = None

    # 全零向量视为模型降级，存储为 None（INSERT 时使用零向量）
    if vec is not None and all(v == 0.0 for v in vec):
        vec = None

    pid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps({
        "data": text,
        "user_id": user,
        "content_hash": content_hash,
        "source": "memory",
        "created_at": now,
    })
    vec_to_store = vec if vec is not None else [0.0] * 512

    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO sinovec (id, vector, payload, source)
                VALUES (%s, %s::vector, %s::jsonb, 'memory')
            """, (pid, vec_to_store, payload))
            conn.commit()
            _log_lineage(pid, "extract", reason="manual_add",
                        details={"user": user, "chars": len(text)})
        finally:
            cur.close()

    logger.info(f"添加记忆: {pid} ({len(text)} chars)")
    return pid


def cmd_stats() -> dict:
    """返回统计信息"""
    # try 块外初始化，防止 SQL 失败时 NameError
    total = 0
    recall_sum = None
    recall_max = 0
    hot_24h = 0
    by_user: dict = {}
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT COUNT(*), SUM(recall_count), MAX(recall_count) FROM sinovec"
            )
            total, recall_sum, recall_max = cur.fetchone()
            cur.execute(
                "SELECT COUNT(*) FROM sinovec "
                "WHERE last_access_time > NOW() - INTERVAL '24 hours'"
            )
            hot_24h = cur.fetchone()[0]
            cur.execute("""
                SELECT payload->>'user_id' as user_id, COUNT(*)
                FROM sinovec
                GROUP BY payload->>'user_id'
                ORDER BY COUNT(*) DESC
                LIMIT 10
            """)
            by_user = {str(r[0]): r[1] for r in cur.fetchall()}
        finally:
            cur.close()

    # 修复：统计有过召回记录的记忆条数（而非 sum(1 for ...)）
    recall_hit_count = 0
    if recall_sum and recall_sum > 0:
        with get_conn() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    "SELECT COUNT(*) FROM sinovec WHERE recall_count > 0"
                )
                recall_hit_count = cur.fetchone()[0] or 0
            finally:
                cur.close()

    return {
        "total": total or 0,
        "recall_total": recall_sum or 0,
        "recall_max": recall_max or 0,
        "recall_hit_count": recall_hit_count,
        "hot_24h": hot_24h or 0,
        "by_user": by_user,
    }


def cmd_delete(mem_id: str) -> bool:
    """删除指定 ID 的记忆"""
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM sinovec WHERE id = %s", (mem_id,))
            deleted = cur.rowcount > 0
            conn.commit()
            _log_lineage(mem_id, "delete", reason="manual_delete")
        finally:
            cur.close()
    return deleted


def cmd_list(limit: int = 20) -> list[dict]:
    """列出最近的 N 条记忆"""
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, payload->>'data' as data,
                       payload->>'user_id' as user_id,
                       payload->>'created_at' as created_at,
                       source
                FROM sinovec
                ORDER BY created_at DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
        finally:
            cur.close()
    return [
        {"id": str(r[0]), "data": r[1], "user_id": r[2],
         "created_at": r[3], "source": r[4]}
        for r in rows
    ]


def cmd_summarize(query: str, top_k: int = 5) -> dict:
    """基于记忆上下文生成摘要"""
    memories = cmd_search(query, top_k=top_k, use_rerank=False, use_expand=False)
    if not memories:
        return {"query": query, "summary": "未找到相关记忆", "memories": []}

    context = "\n".join(f"- {m['data'][:200]}" for m in memories)
    prompt = (
        f"基于以下记忆上下文，用一段话总结用户的需求或偏好：\n{context}\n\n"
        "直接输出总结，不要解释。"
    )

    try:
        from sinovec_core.llm import _ollama_generate
        summary = _ollama_generate(prompt)
    except Exception:
        summary = memories[0]["data"][:200] if memories else "未找到相关记忆"

    return {"query": query, "summary": summary, "memories": memories}
