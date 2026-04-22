# ── SinoVec 检索核心模块 ────────────────────────────────────────────
"""
检索核心：
  - 向量搜索（pgvector ANN）
  - BM25 全文检索（zhparser/simple 分词）
  - 动态权重（根据查询类型自动选择向量/BM25 权重）
  - MMR 多样性去重
"""
import logging
import re
import math
from datetime import datetime, timezone
from typing import Optional

from sinovec_core.db import get_conn, TS_CONFIG
from sinovec_core.llm import (
    generate_vector,
    _query_expand,
    _jieba_tokenize,
    _is_low_quality_query,
    _rerank,
    temporal_decay_score,
    _increment_access,
)
from sinovec_core.constants import (
    VEC_W_SHORT,
    BM25_W_SHORT,
    VEC_W_PROPER,
    BM25_W_PROPER,
    VEC_W_OVERLAP_LO,
    BM25_W_OVERLAP_LO,
    VEC_W_DEFAULT,
    BM25_W_DEFAULT,
    OVERLAP_LO,
    TOP_K_RERANK,
    MMR_LAMBDA,
)

logger = logging.getLogger(__name__)


def _escape_like(text: str) -> str:
    """
    转义 ILIKE 中的通配符 %、_ 以及单引号（防止 SQL 注入）。
    注意：PostgreSQL ILIKE 支持 ESCAPE 子句，但为了简化实现，
    本函数采用全角字符替换方案（%→\uff05, _→\uff3f），
    配合参数化查询，确保 SQL 注入安全。
    """
    # 单引号转义（SQL 标准，防止 SQL 注入）
    text = text.replace("'", "''")
    # ILIKE 通配符 % 和 _ 替换为全角等价字符（不在 ASCII 范围，不触发 ILIKE 通配符）
    text = text.replace("%", "\uff05").replace("_", "\uff3f")
    return text


def _vector_search(
    cur,
    vec: Optional[list[float]],
    top_k: int,
    user_id: Optional[str] = None,
) -> list:
    """
    pgvector ANN 向量搜索。
    返回 [(mid, cosine_dist, payload)] 列表。
    """
    if vec is None:
        return []
    if user_id:
        cur.execute("""
            SELECT id, vector <=> %s::vector as dist, payload
            FROM sinovec
            WHERE payload->>'user_id' = %s
            ORDER BY vector <=> %s::vector
            LIMIT %s
        """, (vec, user_id, vec, top_k))
    else:
        cur.execute("""
            SELECT id, vector <=> %s::vector as dist, payload
            FROM sinovec
            ORDER BY vector <=> %s::vector
            LIMIT %s
        """, (vec, vec, top_k))
    return cur.fetchall()


def _bm25_search(
    cur,
    tsquery_str: Optional[str],
    jieba_terms: list[str],
    top_k: int,
    user_id: Optional[str] = None,
) -> list:
    """
    BM25 全文检索 + ILIKE 手动匹配（pg_trgm GIN 索引已建，ILIKE 走索引扫描）。
    返回 (id, bm25_rank, payload) 元组列表。
    """
    rows = []

    if tsquery_str:
        if user_id:
            cur.execute("""
                SELECT id, GREATEST(ts_rank_cd(fts, query), 0.001) AS bm25_rank, payload
                FROM sinovec, to_tsquery(%s, %s) query
                WHERE fts @@ query AND payload->>'user_id' = %s
                ORDER BY bm25_rank DESC
                LIMIT %s
            """, (TS_CONFIG, tsquery_str, user_id, top_k))
        else:
            cur.execute("""
                SELECT id, GREATEST(ts_rank_cd(fts, query), 0.001) AS bm25_rank, payload
                FROM sinovec, to_tsquery(%s, %s) query
                WHERE fts @@ query
                ORDER BY bm25_rank DESC
                LIMIT %s
            """, (TS_CONFIG, tsquery_str, top_k))
        rows = cur.fetchall()

    if jieba_terms:
        escaped = [_escape_like(w) for w in jieba_terms]
        like_values = [f"%{w}%" for w in escaped]
        conditions = " OR ".join([f"payload->>'data' ILIKE %s" for _ in escaped])
        if user_id:
            cur.execute(f"""
                SELECT id, 0.5 AS bm25_rank, payload
                FROM sinovec
                WHERE ({conditions}) AND payload->>'user_id' = %s
                LIMIT %s
            """, (*like_values, user_id, top_k))
        else:
            cur.execute(f"""
                SELECT id, 0.5 AS bm25_rank, payload
                FROM sinovec
                WHERE {conditions}
                LIMIT %s
            """, (*like_values, top_k))
        rows = cur.fetchall()

    return rows


def _compute_dynamic_weights(
    query: str,
    vec_rows: list,
    bm25_rows: list,
) -> tuple[float, float]:
    """根据查询类型和召回结果分布动态选择向量/BM25 权重。"""
    if len(query) < 10 or _is_low_quality_query(query):
        return VEC_W_SHORT, BM25_W_SHORT

    if vec_rows and bm25_rows:
        vec_ids = {r[0] for r in vec_rows}
        bm25_ids = {r[0] for r in bm25_rows}
        all_ids = vec_ids | bm25_ids
        overlap = len(vec_ids & bm25_ids)
        overlap_ratio = overlap / len(all_ids) if all_ids else 0
        if overlap_ratio >= OVERLAP_LO:
            return VEC_W_OVERLAP_LO, BM25_W_OVERLAP_LO

    return VEC_W_DEFAULT, BM25_W_DEFAULT


def _batch_fetch_vectors(mem_ids: list[str]) -> dict[str, Optional[list[float]]]:
    """
    R7 修复：批量获取一组记忆的向量，避免 N+1 查询。
    返回 {mid: vec_or_None}。
    """
    if not mem_ids:
        return {}
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, vector FROM sinovec WHERE id = ANY(%s::uuid[])",
                ([str(m) for m in mem_ids],)
            )
            rows = cur.fetchall()
            cur.close()
        result: dict[str, Optional[list[float]]] = {str(m): None for m in mem_ids}
        for mid, vec in rows:
            if vec is not None:
                v = vec.tolist() if hasattr(vec, 'tolist') else list(vec)
                result[str(mid)] = v
            else:
                result[str(mid)] = None
        return result
    except Exception:
        return {str(m): None for m in mem_ids}


def _mmr_dedup(
    candidates: list[dict],
    lambda_param: float = 0.5,
    top_n: Optional[int] = None,
) -> list[dict]:
    """
    MMR（Maximal Marginal Relevance）多样性去重。
    lambda_param: 0=只顾多样性，1=只顾相关性

    R7 修复：批量预取所有候选向量，消除 N+1 查询。
    """
    if not candidates:
        return []
    if top_n is None:
        top_n = len(candidates)
    top_n = min(top_n, len(candidates))

    # 批量预取所有候选向量（一次 DB 查询）
    all_ids = [c["id"] for c in candidates]
    vec_map = _batch_fetch_vectors(all_ids)

    def _get_vec(cand: dict) -> Optional[list[float]]:
        return vec_map.get(str(cand["id"]))

    sorted_cands = sorted(candidates, key=lambda x: x.get("score", 0), reverse=True)
    selected, rest = [sorted_cands[0]], sorted_cands[1:]

    for _ in range(top_n - 1):
        if not rest:
            break
        best_idx, best_score = None, -1.0
        sel_vec = _get_vec(selected[-1])

        for i, cand in enumerate(rest):
            cand_vec = _get_vec(cand)
            sim_to_selected = 0.0
            if sel_vec and cand_vec:
                dot = sum(a * b for a, b in zip(sel_vec, cand_vec))
                norm_sel = math.sqrt(sum(a * a for a in sel_vec))
                norm_cand = math.sqrt(sum(b * b for b in cand_vec))
                if norm_sel > 0 and norm_cand > 0:
                    sim_to_selected = dot / (norm_sel * norm_cand)

            mmr_score = lambda_param * cand.get("score", 0) - (1 - lambda_param) * sim_to_selected
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i

        if best_idx is not None:
            selected.append(rest.pop(best_idx))

    return selected


def cmd_search(
    query: str,
    top_k: int = 5,
    use_rerank: bool = True,
    user_id: Optional[str] = None,
    use_expand: bool = True,
) -> list[dict]:
    """
    SinoVec 混合语义检索：
    1. LLM 查询扩展（可选）
    2. 并行向量搜索 + BM25 全文检索
    3. 动态权重融合
    4. MMR 多样性去重
    5. LLM 重排（可选）
    """
    # 查询扩展
    expanded = _query_expand(query) if use_expand else []

    # 向量生成（扩展词合并查询）
    vec: Optional[list[float]] = None
    try:
        vec = generate_vector(query)
    except RuntimeError:
        vec = None  # 模型降级：向量为 None，两路检索退化为纯 BM25

    # 两路检索
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            # 过滤 tsquery 操作符，防止操作符注入导致 to_tsquery() 语法错误
            safe_terms = [re.sub(r"[&|!:()]", "", t) for t in expanded]
            tsquery_str = " & ".join(t for t in safe_terms if t) if (use_expand and expanded) else None
            jieba_terms = [] if _is_low_quality_query(query) else _jieba_tokenize(query)
            vec_rows = _vector_search(cur, vec, top_k * 3, user_id=user_id)
            bm25_rows = _bm25_search(cur, tsquery_str, jieba_terms, top_k * 3, user_id=user_id)
        finally:
            cur.close()

    # 动态权重
    vec_w, bm25_w = _compute_dynamic_weights(query, vec_rows, bm25_rows)

    # 归一化 + 加权融合
    vec_scores = [1 - r[1] for r in vec_rows] if vec_rows else []
    bm25_scores = [r[1] for r in bm25_rows]
    max_vec = max(vec_scores) if vec_scores else 1.0
    max_bm25 = max(bm25_scores) if bm25_scores else 1.0

    merged: dict[str, dict] = {}
    for mid, dist, payload in vec_rows:
        mid_str = str(mid)
        score = (1 - dist) / max_vec if max_vec > 0 else 0.0
        merged[mid_str] = {
            "id": mid_str,
            "vector_score": score,
            "bm25_score": 0.0,
            "score": vec_w * score,
            "payload": payload,
        }

    for mid, bm25_rank, payload in bm25_rows:
        mid_str = str(mid)
        norm_bm25 = bm25_rank / max_bm25 if max_bm25 > 0 else 0.0
        if mid_str in merged:
            merged[mid_str]["bm25_score"] = norm_bm25
            merged[mid_str]["score"] = (
                vec_w * merged[mid_str]["vector_score"] + bm25_w * norm_bm25
            )
        else:
            merged[mid_str] = {
                "id": mid_str,
                "vector_score": 0.0,
                "bm25_score": norm_bm25,
                "score": bm25_w * norm_bm25,
                "payload": payload,
            }

    # 扩展词命中奖励
    for term in expanded:
        term_lower = term.lower()
        for item in merged.values():
            if term_lower in item["payload"].get("data", "").lower():
                item["score"] += 0.05

    # 时间衰减
    now_utc_str = datetime.now(timezone.utc).isoformat()
    for item in merged.values():
        created = item["payload"].get("created_at", now_utc_str)
        item["score"] *= temporal_decay_score(created)

    # 排序 + MMR 去重
    candidates = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
    candidates = _mmr_dedup(candidates, lambda_param=MMR_LAMBDA, top_n=top_k * 2)

    # LLM 重排
    if use_rerank and len(candidates) >= max(5, top_k * 2):
        candidates = _rerank(query, candidates)
        candidates.sort(
            key=lambda x: x.get("rerank_score", x["score"]),
            reverse=True,
        )

    # 截取 top_k 并填充字段
    top_results = candidates[:top_k]
    mem_ids = []
    for r in top_results:
        payload = r.pop("payload", {})
        r["data"] = payload.get("data", "")
        r["user_id"] = payload.get("user_id", "")
        r["created_at"] = payload.get("created_at", "")
        r["source"] = payload.get("source", "memory")
        mem_ids.append(r["id"])

    # 批量更新访问热度
    if mem_ids:
        _increment_access(mem_ids)

    return top_results
