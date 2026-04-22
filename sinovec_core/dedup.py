# ── SinoVec 去重与热度管理模块 ─────────────────────────────────────
"""
去重策略：
  - cmd_dedup: 语义+时效去重（轻量，LIMIT 200 条）
  - cmd_dedup_deep: 深度去重（全量扫描，构建向量相似簇）
热度管理：
  - cmd_promote_by_heat: 按访问频率晋升记忆层级（HOT/WARM/COLD）
  - cmd_organize: 每日整理（热度流转 + 浅度去重）
"""
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from sinovec_core.db import get_conn, _locked_open
from sinovec_core.llm import _log_lineage, temporal_decay_score
from sinovec_core.constants import (
    COSINE_DIST_MERGE, COSINE_DIST_DEEP,
    HOT_RATIO, WARM_RATIO,
    DECAY_HALF_LIFE_DAYS,
    WORKSPACE_ENV,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# 去重
# ══════════════════════════════════════════════════════════════════

def cmd_dedup() -> dict:
    """
    语义+时效去重：
    1. cosine_dist < COSINE_DIST_MERGE（即 sim > 0.85）→ 时效远的直接合并
    2. cosine_dist < COSINE_DIST_NEAR（即 sim > 0.9）→ 时效近的保留较完整的一条
    3. 用 pgvector 的 <=> 算距离，不重复计算 similarity
    """
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, vector, payload->>'created_at' as created_at
                FROM sinovec
                WHERE source = 'memory'
                ORDER BY payload->>'created_at' DESC
                LIMIT 200
            """)
            rows = cur.fetchall()
        finally:
            cur.close()

    merged = 0
    skipped = 0
    deleted_ids: set[str] = set()  # 避免重复删除

    for old_id, old_vec, created_at in rows:
        if old_id in deleted_ids:
            continue

        old_vec_list = _vec_to_list(old_vec)
        if not old_vec_list:
            continue

        with get_conn() as conn:
            cur = conn.cursor()
            try:
                cur.execute("""
                    SELECT id, vector, payload->>'created_at'
                    FROM sinovec
                    WHERE id != %s AND source = 'memory'
                    ORDER BY payload->>'created_at' DESC
                    LIMIT 20
                """, (str(old_id),))
                neighbors = cur.fetchall()
            finally:
                cur.close()

        for nid, n_vec, n_created_at in neighbors:
            if nid in deleted_ids:
                continue
            n_vec_list = _vec_to_list(n_vec)
            if not n_vec_list:
                continue

            dist = _cosine_dist(old_vec_list, n_vec_list)
            if dist is None or dist >= COSINE_DIST_MERGE:
                continue

            old_time = created_at or "1970-01-01T00:00:00Z"
            new_time = n_created_at or "1970-01-01T00:00:00Z"
            keep_id, del_id = (str(old_id), str(nid)) if old_time > new_time else (str(nid), str(old_id))

            if del_id in deleted_ids:
                continue

            try:
                with get_conn() as conn:
                    cur = conn.cursor()
                    try:
                        cur.execute("DELETE FROM sinovec WHERE id = %s", (del_id,))
                        conn.commit()
                    finally:
                        cur.close()
                deleted_ids.add(del_id)
                _log_lineage(del_id, "merge", f"cos_dist={dist:.4f}",
                             target_id=keep_id,
                             details={"dist": float(dist)})
                merged += 1
            except Exception:
                skipped += 1
                break

    return {"merged": merged, "skipped": skipped}


def _vec_to_list(vec):
    """将向量转为 list[float]"""
    if vec is None:
        return None
    if hasattr(vec, 'tolist'):
        return vec.tolist()
    if isinstance(vec, (memoryview, list)):
        return list(vec)
    return None


def _cosine_dist(v1: list[float], v2: list[float]) -> float | None:
    """计算两个向量的余弦距离（出错返回 None）"""
    try:
        a = [float(x) for x in v1]
        b = [float(x) for x in v2]
        import math
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return None
        return 1.0 - dot / (norm_a * norm_b)  # cosine_distance = 1 - cosine_similarity
    except Exception:
        return None


def _build_clusters(conn, cur, threshold: float) -> list[set]:
    """构建所有记忆的向量相似簇。返回 clusters: list[set]"""
    cur.execute("SELECT id, vector, payload->>'data' FROM sinovec")
    all_rows = cur.fetchall()
    print(f"📊 共 {len(all_rows)} 条记忆，开始向量最近邻搜索...")
    clusters: list[set] = []
    processed: set = set()

    for i, (mid, vec_raw, _) in enumerate(all_rows):
        if mid in processed:
            continue
        vec_str = _vec_to_list(vec_raw)
        if vec_str is None:
            continue
        try:
            cur.execute("""
                SELECT id, vector <=> %s::vector as dist
                FROM sinovec WHERE id != %s
                ORDER BY vector <=> %s::vector LIMIT 20
            """, (vec_str, mid, vec_str))
            neighbors = cur.fetchall()
        except Exception as e:
            logger.warning(f"向量最近邻查询失败（mid={mid}）: {e}")
            neighbors = []

        cluster = {mid}
        for nid, dist in neighbors:
            if dist < threshold and nid not in processed:
                cluster.add(nid)
        clusters.append(cluster)
        processed.update(cluster)
        if (i + 1) % 200 == 0:
            print(f"  已处理 {i+1}/{len(all_rows)} 条...")
    return clusters


def _select_deletions(conn, cur, clusters: list[set]) -> list:
    """每个簇保留最长一条，其余标记删除"""
    to_delete = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        best_id, best_len = None, 0
        for mid in cluster:
            try:
                cur.execute("SELECT payload->>'data' FROM sinovec WHERE id = %s", (mid,))
                row = cur.fetchone()
                if row and row[0]:
                    data_len = len(row[0])
                    if data_len > best_len:
                        best_len = data_len
                        best_id = mid
            except Exception:
                pass
        if best_id is not None:
            for mid in cluster:
                if mid != best_id:
                    to_delete.append(mid)
    return to_delete


def _preview_clusters(conn, cur, clusters: list[set], shown: int = 5) -> int:
    """预览前 N 个簇（调试用）"""
    shown_count = 0
    for cluster in clusters:
        if len(cluster) < 2 or shown_count >= shown:
            break
        print(f"\n  簇 #{shown_count+1}（{len(cluster)} 条）:")
        for mid in list(cluster)[:5]:
            try:
                cur.execute("SELECT payload->>'data' FROM sinovec WHERE id = %s", (mid,))
                row = cur.fetchone()
                if row:
                    print(f"    [{mid[:8]}] {str(row[0])[:60]}...")
            except Exception:
                pass
        shown_count += 1
    return shown_count


def cmd_dedup_deep(threshold: float = COSINE_DIST_DEEP, dry_run: bool = True) -> dict:
    """
    基于 pgvector 索引的近似去重（非自连接，O(n·k·log n)）
    threshold: 向量距离阈值，默认 0.1（约=cosine相似度0.9）
    dry_run: True=仅报告，False=执行删除
    """
    # R5 修复：参数校验，防止异常阈值导致不可预测行为
    if not (0.0 < threshold <= 1.0):
        raise ValueError(f"threshold 必须介于 0 和 1 之间，得到 {threshold}")
    with get_conn() as conn:
        cur = None
        try:
            cur = conn.cursor()
            clusters = _build_clusters(conn, cur, threshold)
            to_delete = _select_deletions(conn, cur, clusters)
            _preview_clusters(conn, cur, clusters, shown=5)
        finally:
            if cur is not None:
                cur.close()

    dup_groups = sum(1 for c in clusters if len(c) > 1)
    print(f"\n🔍 发现 {len(clusters)} 个簇，其中 {dup_groups} 个含重复")
    print(f"📋 建议删除：{len(to_delete)} 条")

    if dry_run:
        print("\n⚠️ dry_run=True，未执行删除。加 --no-dry-run 执行实际删除")
        return {"clusters": len(clusters), "to_delete": len(to_delete), "dry_run": True}

    if to_delete:
        with get_conn() as conn2:
            cur2 = None
            try:
                cur2 = conn2.cursor()
                cur2.executemany(
                    "DELETE FROM sinovec WHERE id = %s",
                    [(mid,) for mid in to_delete]
                )
                conn2.commit()
                print(f"\n✅ 已删除 {len(to_delete)} 条重复记忆")
            except Exception as e:
                print(f"\n❌ 删除失败（可能是外键依赖）：{e}")
                conn2.rollback()
            finally:
                if cur2 is not None:
                    cur2.close()
    else:
        print("\n✅ 无重复可删")

    return {"clusters": len(clusters), "deleted": len(to_delete), "dry_run": False}


# ══════════════════════════════════════════════════════════════════
# 热度管理（层记忆文件）
# ══════════════════════════════════════════════════════════════════

def _read_layer_entries(layer_file: Path) -> list[tuple]:
    """读取层记忆文件，返回 (原始行, 日期字符串, 文本内容) 列表"""
    if not layer_file.exists():
        return []
    entries = []
    with _locked_open(layer_file, "r", "utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("- ["):
                m = re.match(r"- \[(\d{4}-\d{2}-\d{2})[^\]]*\] \[([^\]]*)\] (.+)", line)
                if m:
                    entries.append((line, m.group(1), m.group(3)))
    return entries


def _write_layer_entries(layer_file: Path, entries: list[tuple[str, str]]) -> None:
    """追加写入层记忆文件（新条目追加到文件尾）"""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _locked_open(layer_file, "a", "utf-8") as f:
        for date_str, text in entries:
            # date_str 优先用传入值，否则用当天日期
            date = date_str or now_str
            f.write(f"- [{date}] [auto] {text}\n")


def _append_layer_entries_dedup(layer_file: Path, new_lines: list[str]) -> int:
    """
    追加新条目到层文件，自动去重（同一天相同内容不重复追加）。
    返回实际追加的条目数。
    """
    if not new_lines:
        return 0
    existing = _read_layer_entries(layer_file)
    existing_texts = {text for _, _, text in existing}
    added = 0
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _locked_open(layer_file, "a", "utf-8") as f:
        for line_text in new_lines:
            if line_text not in existing_texts:
                f.write(f"- [{now_str}] [auto] {line_text}\n")
                added += 1
    return added


def cmd_promote_by_heat() -> dict:
    """
    按访问频率和时效晋升 HOT/WARM 层。
    逻辑：
    - HOT 层（10%）：decay_weight >= HOT_RATIO
    - WARM 层（60%）：decay_weight >= WARM_RATIO
    - 其余降入 COLD 层

    热记忆文件写入安装目录（$PREFIX/memory/），而非 workspace，
    原因：避免 workspace 跨用户权限问题（sinovec 服务用户 vs openclaw 用户）。
    """
    # 热记忆文件统一写到安装目录（由 install.sh chown sinovec:sinovec）
    _sinovec_home = os.environ.get("SINOVEC_HOME", "/opt/SinoVec")
    hot_file = Path(_sinovec_home) / "memory" / "hot.md"
    warm_file = Path(_sinovec_home) / "memory" / "warm.md"
    cold_file = Path(_sinovec_home) / "memory" / "cold.md"

    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, payload->>'data' as data,
                       created_at, access_count
                FROM sinovec
                WHERE source = 'memory' AND access_count > 0
                ORDER BY access_count DESC
                LIMIT 200
            """)
            rows = cur.fetchall()
        finally:
            cur.close()

    hot_entries: list[tuple[str, str]] = []
    warm_entries: list[tuple[str, str]] = []
    cold_entries: list[tuple[str, str]] = []
    moved = {"hot": 0, "warm": 0, "cold": 0}

    for _mid, data, created_at, access_count in rows:
        if not data:
            continue
        decay = temporal_decay_score(created_at, DECAY_HALF_LIFE_DAYS)
        decay_weight = decay * (access_count or 1)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if decay_weight >= HOT_RATIO:
            hot_entries.append((date_str, data))
            moved["hot"] += 1
        elif decay_weight >= WARM_RATIO:
            warm_entries.append((date_str, data))
            moved["warm"] += 1
        else:
            cold_entries.append((date_str, data))
            moved["cold"] += 1

    for path, entries in [
        (hot_file, hot_entries),
        (warm_file, warm_entries),
        (cold_file, cold_entries),
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        if entries:
            _append_layer_entries_dedup(path, [text for _, text in entries])

    return {"total": len(rows), "moved": moved}


def cmd_organize() -> dict:
    """每日整理：热度流转 + 浅度去重"""
    r1 = cmd_promote_by_heat()
    r2 = cmd_dedup()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "moved": r1.get("moved", {}),
        "dedup": r2,
    }
