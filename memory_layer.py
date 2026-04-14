#!/usr/bin/env python3
"""
SinoVec - 中文语义记忆系统
核心 API 服务
"""

import os, sys, json, threading, queue, logging, uuid
from datetime import datetime, timezone
from socketserver import ThreadingMixIn
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ── 配置 ──────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host": os.getenv("MEMORY_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("MEMORY_DB_PORT", "5432")),
    "database": os.getenv("MEMORY_DB_NAME", "memory"),
    "user": os.getenv("MEMORY_DB_USER", "postgres"),
    "password": os.getenv("MEMORY_DB_PASS", ""),
}

VEC_DIM = 512  # bge-small-zh-v1.5
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── 向量生成 ─────────────────────────────────────────────────────────
_fastembed_model = None
_fastembed_lock = threading.Lock()
_fa_cache = {}

def generate_vector(text: str) -> list[float]:
    """通过 FastEmbed BAAI/bge-small-zh-v1.5 生成 512 维 embedding"""
    key = text[:80]
    with _fastembed_lock:
        if key in _fa_cache:
            return _fa_cache[key]
        global _fastembed_model
        if _fastembed_model is None:
            hf_proxy = os.getenv("HF_HUB_PROXY", "")
            if hf_proxy:
                os.environ["HF_HUB_PROXY"] = hf_proxy
            from fastembed import TextEmbedding
            _fastembed_model = TextEmbedding("BAAI/bge-small-zh-v1.5")
            logger.info("Embedding 模型加载完成: BAAI/bge-small-zh-v1.5")
        arr = list(_fastembed_model.embed([text]))[0]
        emb = [float(x) for x in arr]
        _fa_cache[key] = emb
    return emb

# ── 数据库连接池 ─────────────────────────────────────────────────────
import psycopg2
from psycopg2 import pool

_db_pool = None

def get_conn():
    global _db_pool
    if _db_pool is None:
        _db_pool = psycopg2.pool.ThreadedConnectionPool(1, 20, **DB_CONFIG)
        logger.info("数据库连接池已初始化（1-20连接）")
    return _db_pool.getconn()

def put_conn(conn):
    _db_pool.putconn(conn)

# ── 搜索核心 ─────────────────────────────────────────────────────────
def _is_low_quality_query(query: str) -> bool:
    return len(query.strip()) < 2

def _vector_search(cur, vec: list, top_k: int) -> list:
    cur.execute("""
        SELECT id, vector, vector <=> %s::vector AS vec_dist, payload
        FROM mem0
        ORDER BY vector <=> %s::vector
        LIMIT %s
    """, (vec, vec, top_k * 2))
    return cur.fetchall()

def _bm25_search(cur, query: str, top_k: int) -> list:
    import jieba
    terms = [w for w in jieba.cut(query) if len(w) > 1]
    if not terms:
        return []
    tsquery = ' & '.join(terms)
    cur.execute("""
        SELECT id, GREATEST(ts_rank_cd(fts, query), 0.001) AS bm25_rank, payload
        FROM mem0, to_tsquery('simple', %s) query
        WHERE fts @@ query
        ORDER BY bm25_rank DESC
        LIMIT %s
    """, (tsquery, top_k))
    return cur.fetchall()

def search_memories(query: str, top_k: int = 5) -> list[dict]:
    if _is_low_quality_query(query):
        return []
    conn = get_conn()
    cur = conn.cursor()
    try:
        vec = generate_vector(query)
        vec_rows = _vector_search(cur, vec, top_k)
        bm25_rows = _bm25_search(cur, query, top_k)
        vec_ids = {r[0] for r in vec_rows}
        bm25_ids = {r[0] for r in bm25_rows}
        overlap = len(vec_ids & bm25_ids) / len(vec_ids | bm25_ids) if vec_ids and bm25_ids else 0
        vec_w = 0.7 if overlap < 0.3 else 0.5
        bm25_w = 0.3 if overlap < 0.3 else 0.5
        scores = {}
        for rank, row in enumerate(vec_rows):
            scores[row[0]] = scores.get(row[0], 0) + vec_w * (1 - rank / len(vec_rows))
        for rank, row in enumerate(bm25_rows):
            scores[row[0]] = scores.get(row[0], 0) + bm25_w * (1 - rank / len(bm25_rows))
        sorted_ids = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        id_map = {r[0]: r[3] for r in vec_rows + bm25_rows}
        return [
            {"id": str(k), "score": round(v, 4), "data": id_map[k].get("data", "")}
            for k, v in sorted_ids
        ]
    finally:
        cur.close()
        put_conn(conn)

def add_memory(text: str, user: str = "默认用户", source: str = "memory") -> str:
    """添加单条记忆"""
    conn = get_conn()
    cur = conn.cursor()
    try:
        vec = generate_vector(text)
        pid = str(uuid.uuid4())
        payload = json.dumps({"data": text, "user_id": user, "source": source})
        cur.execute("""
            INSERT INTO mem0 (id, vector, payload, fts)
            VALUES (%s, %s::vector, %s::jsonb, to_tsvector('simple', %s))
        """, (pid, vec, payload, text))
        conn.commit()
        return pid
    finally:
        cur.close()
        put_conn(conn)

# ── HTTP API ─────────────────────────────────────────────────────────
class MemoryHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.info(f"{self.address_string()} - {format % args}")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.send_json({"status": "ok"})
        elif parsed.path == "/search":
            params = parse_qs(parsed.query)
            query = params.get("q", [""])[0]
            top_k = int(params.get("top_k", [5])[0])
            if not query:
                self.send_json({"error": "missing q param"}, 400)
                return
            try:
                results = search_memories(query, top_k)
                self.send_json({"count": len(results), "results": results})
            except Exception as e:
                logger.error(f"搜索失败: {e}")
                self.send_json({"error": str(e)}, 500)
        elif parsed.path == "/stats":
            conn = get_conn()
            cur = conn.cursor()
            try:
                cur.execute("SELECT COUNT(*) FROM mem0")
                count = cur.fetchone()[0]
                self.send_json({"total": count})
            finally:
                cur.close()
                put_conn(conn)
        else:
            self.send_json({"error": "not found"}, 404)

    def send_json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def cmd_serve(host: str = "127.0.0.1", port: int = 18793):
    server = ThreadedHTTPServer((host, port), MemoryHandler)
    logger.info(f"SinoVec API 启动: http://{host}:{port}")
    server.serve_forever()

# ── CLI ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SinoVec 记忆系统")
    sub = parser.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="启动 HTTP API 服务")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=18793)

    p_add = sub.add_parser("add", help="添加记忆")
    p_add.add_argument("text")
    p_add.add_argument("--user", default="默认用户")

    p_stats = sub.add_parser("stats", help="查看统计")

    args = parser.parse_args()

    if args.cmd == "serve":
        cmd_serve(args.host, args.port)
    elif args.cmd == "add":
        pid = add_memory(args.text, args.user)
        print(f"记忆已添加: {pid}")
    elif args.cmd == "stats":
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM mem0")
        print(f"总记忆数: {cur.fetchone()[0]}")
        cur.close()
        put_conn(conn)
    else:
        parser.print_help()
