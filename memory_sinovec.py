#!/usr/bin/env python3
"""
SinoVec - 高精度中文语义记忆系统

核心能力：
  - 向量 + BM25 混合检索（动态权重）
  - LLM 查询扩展（可选，提升召回率）
  - LLM 重排（可选，对候选结果二次打分）
  - 时间衰减（近期记忆权重更高）
  - MMR 多样性去重（避免结果同质化）
  - 访问热度追踪（access_count + last_access_time）
  - 记忆血缘记录（memory_lineage 表）
  - 语义+时效去重（自动合并近似记忆）
  - 自动记忆提取（从会话中提炼有价值信息）
  - 会话历史索引（索引 AI 回复片段）
  - HTTP API（供 OpenClaw 等 Agent 框架调用）
"""

import os
import sys

# ── 兼容旧导入路径 ────────────────────────────────────────────────
# 将本文件所在的包路径添加到 sys.path（支持 "from memory_sinovec import xxx" 导入）
_PKG_DIR = os.path.join(os.path.dirname(__file__), "sinovec_core")
if os.path.isdir(_PKG_DIR) and _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

# ── 重新导出所有公开 API（保持向后兼容）────────────────────────────
# DB 基础设施
from sinovec_core.db import get_conn, TS_CONFIG, _locked_open, _LockedFile

# 常量
from sinovec_core.constants import (
    COSINE_DIST_NEAR, COSINE_DIST_MERGE, COSINE_DIST_DEEP,
    COSINE_DIST_SESS_GAP, OVERLAP_LO,
    DECAY_HALF_LIFE_DAYS, DEDUP_WINDOW_HOURS,
    HOT_MAX_DAYS, WARM_MAX_DAYS, ACCESS_INTERVAL_HOURS, LINEAGE_CLEANUP_DAYS,
    HOT_RATIO, WARM_RATIO,
    MIN_CONTENT_CHARS, SHORT_CONTENT_CHARS,
    VEC_W_SHORT, BM25_W_SHORT, VEC_W_PROPER, BM25_W_PROPER,
    VEC_W_OVERLAP_LO, BM25_W_OVERLAP_LO, VEC_W_DEFAULT, BM25_W_DEFAULT,
    RERANK_MIN_CANDIDATES, RERANK_DEFAULT_SCORE, MMR_LAMBDA,
    TOP_K_RERANK, QUERY_EXPANSION_MAX, MIN_QUERY_TERM_LEN, RECALL_ANALYSIS_LIMIT,
    OLLAMA_TEMPERATURE, OLLAMA_MAX_TOKENS,
    WORKSPACE_ENV,
)

# LLM / Ollama
from sinovec_core.llm import (
    generate_vector, compute_hash,
    _ollama_check_available, _ollama_model_exists, _ollama_generate,
    _query_expand, _jieba_tokenize, _is_low_quality_query,
    _log_lineage, _rerank, temporal_decay_score,
    _increment_access,
)

# 检索核心
from sinovec_core.search import (
    cmd_search, _escape_like,
    _vector_search, _bm25_search, _compute_dynamic_weights, _mmr_dedup,
)

# 去重与热度
from sinovec_core.dedup import (
    cmd_dedup, cmd_dedup_deep,
    _build_clusters, _select_deletions, _preview_clusters,
    cmd_promote_by_heat, cmd_organize,
    _read_layer_entries, _write_layer_entries, _append_layer_entries_dedup,
)

# 分析
from sinovec_core.analysis import (
    cmd_recall_analysis, cmd_session_l1_gap, cmd_lineage_cleanup,
)

# 命令
from sinovec_core.commands import (
    cmd_add, cmd_stats, cmd_delete, cmd_list, cmd_summarize,
)

# HTTP 服务器
from sinovec_core.http_server import _run_http_server


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="SinoVec 中文语义记忆系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", metavar="<command>")

    # 添加记忆
    add_p = sub.add_parser("add", help="添加记忆")
    add_p.add_argument("text", help="记忆内容")
    add_p.add_argument("--user", default="主人", help="用户标识")
    add_p.add_argument("--force", action="store_true", help="强制添加（绕过质量门）")

    # 搜索记忆
    search_p = sub.add_parser("search", help="搜索记忆")
    search_p.add_argument("query", help="查询内容")
    search_p.add_argument("--top-k", type=int, default=5, help="返回数量")
    search_p.add_argument("--json", action="store_true", help="JSON 输出")
    search_p.add_argument("--no-rerank", action="store_true", help="禁用 LLM 重排")
    search_p.add_argument("--no-expand", action="store_true", help="禁用查询扩展")
    search_p.add_argument("--user-id", dest="user_id", help="用户过滤")

    # 统计
    sub.add_parser("stats", help="记忆统计")

    # 删除
    del_p = sub.add_parser("delete", help="删除记忆")
    del_p.add_argument("id", help="记忆 ID")

    # 列出
    list_p = sub.add_parser("list", help="列出最近记忆")
    list_p.add_argument("--limit", type=int, default=20)

    # 摘要
    sum_p = sub.add_parser("summarize", help="基于记忆生成摘要")
    sum_p.add_argument("query", help="查询内容")
    sum_p.add_argument("--top-k", type=int, default=5)

    # 去重
    sub.add_parser("dedup", help="浅度去重（语义+时效）")
    dedup_deep_p = sub.add_parser("dedup-deep", help="深度去重（全量向量聚类）")
    dedup_deep_p.add_argument("--threshold", type=float, default=COSINE_DIST_DEEP)
    dedup_deep_p.add_argument("--no-dry-run", dest="no_dry_run", action="store_true",
                              help="执行实际删除（默认 dry-run）")

    # 召回分析
    recall_p = sub.add_parser("recall-analysis", help="召回分析")
    recall_p.add_argument("--limit", type=int, default=RECALL_ANALYSIS_LIMIT)

    # 会话缺口
    gap_p = sub.add_parser("session-gap", help="会话缺口分析")
    gap_p.add_argument("--gap-threshold", type=float, default=COSINE_DIST_SESS_GAP)
    gap_p.add_argument("--overlap-threshold", type=float, default=OVERLAP_LO)
    gap_p.add_argument("--limit", type=int, default=20)

    # 热度晋升
    sub.add_parser("promote-heat", help="按热度晋升记忆层级")

    # 每日整理
    sub.add_parser("organize", help="每日整理（热度流转+去重）")

    # 血缘清理
    cleanup_p = sub.add_parser("lineage-cleanup", help="清理血缘记录")
    cleanup_p.add_argument("--days", type=int, default=LINEAGE_CLEANUP_DAYS)
    cleanup_p.add_argument("--no-dry-run", dest="no_dry_run", action="store_true")

    # HTTP 服务
    serve_p = sub.add_parser("serve", help="启动 HTTP API 服务")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=18793)

    args = parser.parse_args()

    try:
        if args.cmd == "add":
            pid = cmd_add(args.text, user=args.user, force=args.force)
            print(f"✅ 记忆已添加: {pid}")

        elif args.cmd == "search":
            results = cmd_search(
                args.query, top_k=args.top_k,
                use_rerank=not args.no_rerank,
                use_expand=not args.no_expand,
                user_id=args.user_id,
            )
            if args.json:
                output = []
                for r in results:
                    from datetime import datetime, timezone
                    output.append({
                        "id": r['id'],
                        "score": r['score'],
                        "rerank": r.get('rerank_score', None),
                        "user_id": r.get('user_id', ''),
                        "created_at": r.get('created_at', ''),
                        "data": r.get('data', ''),
                    })
                print(json.dumps({
                    "query": args.query,
                    "count": len(output),
                    "results": output
                }, ensure_ascii=False, indent=2))
                return
            if not results:
                print("未找到匹配记忆（或查询质量过低）")
            else:
                print(f"找到 {len(results)} 条记忆：\n")
                for r in results:
                    rs = r.get('rerank_score', 'N/A')
                    print(f"  [{r['id'][:8]}...]  score={r['score']}  rerank={rs}")
                    print(f"  用户: {r['user_id']}  时间: {r['created_at'][:19]}")
                    print(f"  内容: {r['data'][:100]}{'…' if len(r['data']) > 100 else ''}")
                    print()

        elif args.cmd == "stats":
            s = cmd_stats()
            print(f"总记忆条数: {s['total']}")
            for u, n in s.get("by_user", {}).items():
                print(f"  {u}: {n} 条")
            print(f"recall 总次数: {s['recall_total']}  最高: {s['recall_max']}  命中记忆数: {s['recall_hit_count']}")

        elif args.cmd == "delete":
            ok = cmd_delete(args.id)
            print(f"{'✅ 已删除' if ok else '❌ 未找到指定记忆'}")

        elif args.cmd == "list":
            rows = cmd_list(args.limit)
            if not rows:
                print("暂无记忆")
            else:
                for r in rows:
                    print(f"[{r['id'][:8]}] {r['user_id']}  {r['created_at'][:19]}")
                    print(f"  {r['data'][:80]}{'…' if len(r['data']) > 80 else ''}")

        elif args.cmd == "summarize":
            result = cmd_summarize(args.query, args.top_k)
            print(f"📋 查询：{result['query']}\n")
            print(f"💡 摘要：{result['summary']}\n")
            if result['memories']:
                print(f"--- 召回 {len(result['memories'])} 条记忆 ---")

        elif args.cmd == "dedup":
            r = cmd_dedup()
            print(f"去重完成：合并 {r['merged']} 条，跳过 {r['skipped']} 条")

        elif args.cmd == "dedup-deep":
            r = cmd_dedup_deep(threshold=args.threshold, dry_run=not args.no_dry_run)
            print(f"深度去重完成：簇数={r['clusters']}, 待删={r.get('to_delete',0)}, 实际删除={r.get('deleted',0)}")

        elif args.cmd == "recall-analysis":
            cmd_recall_analysis(limit=args.limit)

        elif args.cmd == "session-gap":
            cmd_session_l1_gap(gap_threshold=args.gap_threshold,
                                overlap_threshold=args.overlap_threshold,
                                limit=args.limit)

        elif args.cmd == "promote-heat":
            r = cmd_promote_by_heat()
            print(f"热度流转完成：共处理 {r['total']} 条")
            print(f"  跳过（已晋崑）: {r.get('skipped', 0)} 条")
            for layer, cnt in r['moved'].items():
                if cnt > 0:
                    print(f"  {layer}: {cnt} 条")

        elif args.cmd == "organize":
            r = cmd_organize()
            print(f"每日整理完成 @ {r['timestamp']}")
            for k, v in r['moved'].items():
                if v > 0:
                    print(f"  {k}: {v} 条")
            dd = r.get('dedup', {})
            print(f"  去重: 合并 {dd.get('merged', 0)} 条，跳过 {dd.get('skipped', 0)} 条")

        elif args.cmd == "lineage-cleanup":
            r = cmd_lineage_cleanup(days=args.days, dry_run=not args.no_dry_run)
            print(f"血缘清理完成：total={r['total']} old={r['old']} deleted={r.get('deleted', 0)}")

        elif args.cmd == "serve":
            _run_http_server(args.host, args.port)

        else:
            parser.print_help()

    except Exception as e:
        print(f"❌ 错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
