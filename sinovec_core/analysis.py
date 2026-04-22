# ── SinoVec 分析模块 ────────────────────────────────────────────────
"""
会话缺口分析、召回统计、血缘清理等分析型命令。
"""
import logging
import numpy as np
from datetime import datetime, timezone

from sinovec_core.db import get_conn
from sinovec_core.constants import (
    COSINE_DIST_SESS_GAP, OVERLAP_LO, RECALL_ANALYSIS_LIMIT,
    LINEAGE_CLEANUP_DAYS,
)

logger = logging.getLogger(__name__)


def cmd_recall_analysis(limit: int = RECALL_ANALYSIS_LIMIT) -> None:
    """
    召回分析：统计高召回记忆（被频繁检索的记忆），
    识别召回率低的区域，为调整半衰期提供依据。
    """
    print(f"📊 召回分析（top {limit}）：\n")
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, payload->>'data' as data,
                       recall_count, access_count,
                       EXTRACT(DAYS FROM (NOW()-last_access_time)) as days_since_access
                FROM sinovec
                WHERE recall_count > 0
                ORDER BY recall_count DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
        finally:
            cur.close()

    if not rows:
        print("暂无召回数据")
        return

    total_recall = sum(r[2] or 0 for r in rows)
    print(f"总召回次数: {total_recall}")
    print(f"{'ID':<12} {'recall_count':>12} {'access_count':>14} {'days_ago':>10}  内容摘要")
    print("-" * 80)
    for mid, data, recall_count, access_count, days_ago in rows:
        days_ago_str = f"{int(days_ago)}d" if days_ago else "从未"
        print(f"{str(mid)[:12]:<12} {recall_count:>12} {access_count or 0:>14} {days_ago_str:>10}  {str(data)[:40]}")


def cmd_session_l1_gap(
    gap_threshold: float = COSINE_DIST_SESS_GAP,
    overlap_threshold: float = OVERLAP_LO,
    limit: int = 20,
) -> None:
    """
    会话缺口分析（L1 级别）：
    识别数据库中 source='session' 的连续片段之间存在语义断层的边界。
    gap_threshold: 相邻片段向量距离超过此值认为有语义跳跃（默认 0.3）
    overlap_threshold: 内容重叠率超过此值认为有重复（默认 0.30）
    """
    print("🔍 会话缺口分析...\n")
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT s1.id,
                       s1.payload->>'session_id' as session_id,
                       s1.payload->>'data' as data,
                       s1.vector,
                       s2.id as next_id,
                       s2.payload->>'data' as next_data,
                       s2.vector as next_vector
                FROM sinovec s1
                JOIN sinovec s2 ON
                     s1.payload->>'session_id' = s2.payload->>'session_id'
                     AND s1.payload->>'source_id' < s2.payload->>'source_id'
                     AND NOT EXISTS (
                         SELECT 1 FROM sinovec s3
                         WHERE s3.payload->>'session_id' = s1.payload->>'session_id'
                           AND s3.payload->>'source_id' > s1.payload->>'source_id'
                           AND s3.payload->>'source_id' < s2.payload->>'source_id'
                     )
                WHERE s1.source = 'session' AND s2.source = 'session'
                LIMIT %s
            """, (limit * 2,))
            pairs = cur.fetchall()
        finally:
            cur.close()

    if not pairs:
        print("未找到足够多的相邻会话片段对进行分析")
        return

    gaps = []
    for _sid1, session_id, data1, vec1, _next_sid, data2, vec2 in pairs:
        if not (vec1 and vec2):
            continue
        try:
            v1 = np.array(vec1.tolist() if hasattr(vec1, 'tolist') else vec1)
            v2 = np.array(vec2.tolist() if hasattr(vec2, 'tolist') else vec2)
            norm_sum = np.linalg.norm(v1) + np.linalg.norm(v2)
            cos_dist = float(np.linalg.norm(v1 - v2) / (norm_sum + 1e-8))
        except Exception:
            cos_dist = 1.0

        words1 = set(data1.split())
        words2 = set(data2.split())
        overlap = len(words1 & words2) / max(len(words1 | words2), 1)

        if cos_dist >= gap_threshold or overlap >= overlap_threshold:
            gaps.append({
                "session_id": session_id,
                "gap": cos_dist,
                "overlap": overlap,
                "data1": data1[:60],
                "data2": data2[:60],
            })

    print(f"发现 {len(gaps)} 个会话缺口/重叠：\n")
    for g in gaps[:limit]:
        flag = "🔄 重叠" if g["overlap"] >= overlap_threshold else "⏭ 断层"
        print(f"{flag}  session={g['session_id'][:30]}  gap={g['gap']:.3f}  overlap={g['overlap']:.2f}")
        print(f"  → {g['data1']}...")
        print(f"  ← {g['data2']}...")
        print()


def cmd_lineage_cleanup(days: int = LINEAGE_CLEANUP_DAYS, dry_run: bool = True) -> dict:
    """
    清理血缘记录表：删除 N 天前的操作记录。
    保留近期记录以供审计使用。
    """
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            # 修复：使用参数化查询（INTERVAL 支持参数化）
            cur.execute("""
                SELECT COUNT(*) FROM memory_lineage
                WHERE created_at < NOW() - (INTERVAL '1 day' * %s)
            """, (days,))
            old_count = cur.fetchone()[0]
            if not dry_run:
                cur.execute("""
                    DELETE FROM memory_lineage
                    WHERE created_at < NOW() - (INTERVAL '1 day' * %s)
                """, (days,))
                conn.commit()
                print(f"✅ 已删除 {old_count} 条过期血缘记录")
            else:
                print(f"⚠️  dry_run=True，将删除 {old_count} 条过期血缘记录")
        finally:
            cur.close()

    return {"total": old_count, "old": old_count, "deleted": 0 if dry_run else old_count}
