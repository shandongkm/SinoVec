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
import json
import re
import uuid
import hashlib
import hmac
import argparse
import logging

# 模型下载代理（mihomo）
os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:7890")
os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:7890")
import os
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from functools import lru_cache
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from cachetools import TTLCache

import psycopg2
import numpy as np
import jieba

logger = logging.getLogger(__name__)



# ═══════════════════════════════════════════════════════════
# 魔法数字常量（支持环境变量覆盖，便于生产调优）
# ═══════════════════════════════════════════════════════════

_E = os.getenv

# ── 去重阈值 ──────────────────────────────────────────────
COSINE_DIST_NEAR       = float(_E("MEM_COSINE_DIST_NEAR",     "0.10"))
COSINE_DIST_MERGE      = float(_E("MEM_COSINE_DIST_MERGE",    "0.15"))
COSINE_DIST_DEEP       = float(_E("MEM_COSINE_DIST_DEEP",     "0.10"))
COSINE_DIST_SESS_GAP   = float(_E("MEM_COSINE_DIST_SESS_GAP", "0.30"))
OVERLAP_LO             = float(_E("MEM_OVERLAP_LO",           "0.30"))

# ── 时间与时效 ────────────────────────────────────────────
DECAY_HALF_LIFE_DAYS   = int(_E("MEM_DECAY_HALF_LIFE_DAYS",  "30"))
DEDUP_WINDOW_HOURS     = int(_E("MEM_DEDUP_WINDOW_HOURS",     "1"))
HOT_MAX_DAYS           = int(_E("MEM_HOT_MAX_DAYS",           "2"))
WARM_MAX_DAYS          = int(_E("MEM_WARM_MAX_DAYS",          "7"))
ACCESS_INTERVAL_HOURS  = int(_E("MEM_ACCESS_INTERVAL_HOURS",  "1"))
LINEAGE_CLEANUP_DAYS   = int(_E("MEM_LINEAGE_CLEANUP_DAYS",  "90"))

# ── 层级晋升比例 ──────────────────────────────────────────
HOT_RATIO  = float(_E("MEM_HOT_RATIO",  "0.10"))
WARM_RATIO = float(_E("MEM_WARM_RATIO", "0.60"))

# ── 内容质量门 ────────────────────────────────────────────
MIN_CONTENT_CHARS    = int(_E("MEM_MIN_CONTENT_CHARS",    "15"))
SHORT_CONTENT_CHARS  = int(_E("MEM_SHORT_CONTENT_CHARS",  "30"))

# ── 检索权重 ───────────────────────────────────────────────
BM25_MANUAL_WEIGHT = float(_E("MEM_BM25_MANUAL_WEIGHT", "0.30"))
VEC_W_SHORT  = float(_E("MEM_VEC_W_SHORT",  "0.85")); BM25_W_SHORT  = float(_E("MEM_BM25_W_SHORT",  "0.15"))
VEC_W_PROPER = float(_E("MEM_VEC_W_PROPER", "0.35")); BM25_W_PROPER = float(_E("MEM_BM25_W_PROPER", "0.65"))
VEC_W_OVERLAP_LO = float(_E("MEM_VEC_W_OVERLAP_LO", "0.55")); BM25_W_OVERLAP_LO = float(_E("MEM_BM25_W_OVERLAP_LO", "0.45"))
VEC_W_DEFAULT = float(_E("MEM_VEC_W_DEFAULT", "0.70")); BM25_W_DEFAULT = float(_E("MEM_BM25_W_DEFAULT", "0.30"))

# ── 重排与 MMR ─────────────────────────────────────────────
RERANK_MIN_CANDIDATES = int(_E("MEM_RERANK_MIN_CANDIDATES", "5"))
RERANK_DEFAULT_SCORE  = float(_E("MEM_RERANK_DEFAULT_SCORE", "0.50"))
MMR_LAMBDA            = float(_E("MEM_MMR_LAMBDA",           "0.50"))

# ── 检索参数 ───────────────────────────────────────────────
TOP_K_RERANK           = int(_E("MEM_TOP_K_RERANK",          "20"))
QUERY_EXPANSION_MAX    = int(_E("MEM_QUERY_EXPANSION_MAX",    "5"))
MIN_QUERY_TERM_LEN     = int(_E("MEM_MIN_QUERY_TERM_LEN",     "2"))
SESSION_FRAGMENT_LIMIT = int(_E("MEM_SESSION_FRAGMENT_LIMIT",  "20"))
RECALL_ANALYSIS_LIMIT  = int(_E("MEM_RECALL_ANALYSIS_LIMIT",  "50"))

# ── LLM 配置 ───────────────────────────────────────────────
OLLAMA_TEMPERATURE = float(_E("MEM_OLLAMA_TEMPERATURE", "0.30"))
OLLAMA_MAX_TOKENS  = int(_E("MEM_OLLAMA_MAX_TOKENS",   "500"))

# ═══════════════════════════════════════════════════════════


# ── DB 配置 ──────────────────────────────────────────────
# ── 配置（支持环境变量覆盖）─────────────────────────────────────
_db_pass = os.getenv("MEMORY_DB_PASS", "")
if not _db_pass:
    raise RuntimeError(
        "MEMORY_DB_PASS environment variable is not set. "
        "Please set it and restart. "
        "Example: export MEMORY_DB_PASS=your_secure_password"
    )
DB_CONFIG = {
    "host": os.getenv("MEMORY_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("MEMORY_DB_PORT", "5433")),
    "database": os.getenv("MEMORY_DB_NAME", "memory"),
    "user": os.getenv("MEMORY_DB_USER", "openclaw"),
    "password": _db_pass,
}

# ── 连接池（线程安全）────────────────────────────────────────────
_db_pool = None
_pool_lock = threading.Lock()

def _get_pool():
    global _db_pool
    if _db_pool is None:
        with _pool_lock:
            if _db_pool is None:
                from psycopg2.pool import SimpleConnectionPool
                _db_pool = SimpleConnectionPool(1, 20, **DB_CONFIG)
                logger.info("DB 连接池已初始化（1-20连接）")
                # 预热
                try:
                    _warm = _db_pool.getconn()
                    _warm.cursor().execute("SELECT 1")
                    _warm.cursor().close()
                    _db_pool.putconn(_warm)
                    logger.info("DB 连接池预热完成")
                except Exception as _e:
                    logger.warning(f"连接池预热失败: {_e}")
    return _db_pool

@contextmanager
def get_conn():
    """从连接池获取连接，自动归还（防止连接泄漏）"""
    conn = _get_pool().getconn()
    try:
        yield conn
    finally:
        _get_pool().putconn(conn)


# ── 工作区路径（可环境变量覆盖）──────────────────────────────────
_WORKSPACE_ENV = os.environ.get("MEMORY_WORKSPACE", "/root/.openclaw/workspace")


# ── 文件操作加锁 ───────────────────────────────────────────────
import fcntl as _fcntl

def _locked_open(path: str, mode: str = "r", encoding: str = "utf-8"):
    """
    文件操作加锁上下文管理器
    - 写模式（w/a/w+/a+）：独占锁 LOCK_EX
    - 读模式（r/rb）：共享锁 LOCK_SH
    - 其他模式：不加锁
    """
    if mode in ("w", "a", "w+", "a+"):
        lock_type = _fcntl.LOCK_EX
    elif mode in ("r", "rb"):
        lock_type = _fcntl.LOCK_SH
    else:
        return open(path, mode, encoding=encoding)
    f = open(path, mode, encoding=encoding)
    _fcntl.flock(f.fileno(), lock_type)
    return _LockedFile(f, lock_type)

class _LockedFile:
    """带锁的文件对象，退出时释放锁并关闭"""
    def __init__(self, f, lock_type=_fcntl.LOCK_UN):
        self._f = f
        self._lock_type = lock_type
    def __enter__(self):
        return self._f
    def __exit__(self, *args):
        try:
            _fcntl.flock(self._f.fileno(), _fcntl.LOCK_UN)
        finally:
            self._f.close()

# ── HTTP API（供 OpenClaw Active Memory 插件调用）──────────────────

def _run_http_server(host: str = "127.0.0.1", port: int = 18793) -> None:
    """
    轻量级 HTTP API server，不依赖任何第三方库。
    GET /search?q=...&top_k=3&user_id=主人
    GET /health
    """
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from urllib.parse import urlparse, parse_qs
    from socketserver import ThreadingMixIn

    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    class MemoryHandler(BaseHTTPRequestHandler):
        def _check_auth(self) -> bool:
            """校验 API Key，支持 Bearer token、X-API-Key 或 ?api_key= 参数"""
            expected = os.getenv("MEMORY_API_KEY", "")
            if not expected:
                return True  # 未配置则跳过验证（开发模式）
            # 1. Authorization: Bearer <key>
            auth_header = self.headers.get("Authorization", "")
            token = auth_header.replace("Bearer ", "").strip()
            if token and expected and hmac.compare_digest(token, expected):
                return True
            # 2. X-API-Key: <key>
            token = self.headers.get("X-API-Key", "").strip()
            if token and expected and hmac.compare_digest(token, expected):
                return True
            # 3. ?api_key=<key>
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            token = params.get("api_key", [None])[0] or ""
            if token and expected and hmac.compare_digest(token, expected):
                return True
            return False

        def do_GET(self):
            # 健康检查不需要认证
            if self.path != "/health" and not self._check_auth():
                self._send_json({"error": "unauthorized"}, 401)
                return
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send_json({"status": "ok"})
            elif parsed.path == "/search":
                params = parse_qs(parsed.query)
                query = params.get("q", [""])[0]
                if not query:
                    self._send_json({"error": "missing q param"}, 400)
                    return
                top_k = int(params.get("top_k", ["3"])[0])
                user_id = params.get("user_id", [None])[0] or None
                use_rerank = params.get("rerank", ["1"])[0] != "0"
                use_expand = params.get("expand", ["1"])[0] != "0"
                try:
                    results = cmd_search(query, top_k=top_k, user_id=user_id, use_rerank=use_rerank, use_expand=use_expand)
                    wrapped = {
                        "count": len(results),
                        "results": results,
                    }
                    self._send_json(wrapped)
                except Exception as e:
                    self._send_json({"error": str(e)}, 500)
            elif parsed.path == "/stats":
                try:
                    with get_conn() as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT COUNT(*), SUM(recall_count), MAX(recall_count) FROM mem0 WHERE source = 'memory'")
                        total, recall_sum, recall_max = cur.fetchone()
                        cur.execute("SELECT COUNT(*) FROM mem0 WHERE source = 'memory' AND last_access_time > NOW() - INTERVAL '24 hours'")
                        hot_24h = cur.fetchone()[0]
                        cur.close()
                    self._send_json({
                        "total": total or 0,
                        "recall_total": recall_sum or 0,
                        "recall_max": recall_max or 0,
                        "hot_24h": hot_24h or 0,
                    })
                except Exception as e:
                    self._send_json({"error": str(e)}, 500)
            else:
                self._send_json({"error": "not found"}, 404)

        def _send_json(self, data, code=200):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadedHTTPServer((host, port), MemoryHandler)
    print(f"Memory HTTP API server running on http://{host}:{port} (threaded)", flush=True)
    server.serve_forever()


# ── 结构化日志（RotatingFileHandler + JSON）──────────────────────
_log_file = os.getenv("MEMORY_LOG_FILE", "/var/log/memory_layer.log")
try:
    from logging.handlers import RotatingFileHandler
    _file_handler = RotatingFileHandler(
        _log_file, maxBytes=10*1024*1024, backupCount=5
    )
    _file_handler.setFormatter(logging.Formatter(
        '{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}'
    ))
    logger.addHandler(_file_handler)
    logger.info("文件日志已启用：%s", _log_file)
except Exception as e:
    logger.warning("文件日志初始化失败（需sudo或目录权限）：%s", e)

VEC_DIM = 512  # BAAI/bge-small-zh-v1.5 实际输出512维，与 pgvector 表定义一致

# ── Ollama 并发控制（有界队列 + 信号量）─────────────────────────
_ollama_semaphore = threading.Semaphore(2)  # 最多2并发执行
_ollama_queue = queue.Queue(maxsize=10)       # 最多排队10个请求（防内存雪崩）
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="ollama-")

def _ollama_safe_call(fn, *args, **kwargs):
    """
    带并发控制（Semaphore）和有界队列的 Ollama 调用。
    - 队列满时阻塞等待，不拒绝请求（保证正确性）
    - Semaphore 控制同时执行的 Ollama 请求数
    - 全局队列计数器统计排队中的请求
    """
    # 先获取队列位置（阻塞直到有空间）
    _ollama_queue.put(None)  # None 作为占位符，入队即阻塞其他入队者
    try:
        with _ollama_semaphore:
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                logger.warning(f"ollama 调用失败: {e}，返回降级结果")
                return None  # fallback
            pass
    finally:
        _ollama_queue.get()  # 出队，释放队列空间

# ── Embedding：FastEmbed（BAAI/bge-small-zh-v1.5，512维）────────
_fastembed_model = None
_fastembed_lock = threading.Lock()
_fa_cache = TTLCache(maxsize=1000, ttl=3600)  # 最多1000条，1小时过期

def generate_vector(text: str) -> list[float]:
    """通过 FastEmbed BAAI/bge-small-zh-v1.5 生成 512 维 embedding（本地推理）"""
    key = text[:80]
    with _fastembed_lock:
        if key in _fa_cache:
            return _fa_cache[key]
        global _fastembed_model
        if _fastembed_model is None:
            from fastembed import TextEmbedding
            cache_dir = os.environ.get("FASTEMBED_CACHE_DIR", os.path.expanduser("~/.cache/fastembed"))
            _fastembed_model = TextEmbedding("BAAI/bge-small-zh-v1.5", cache_dir=cache_dir)
            logger.info("Embedding 后端: FastEmbed (BAAI/bge-small-zh-v1.5)")
    arr = list(_fastembed_model.embed([text]))[0]
    emb = [float(x) for x in arr]
    _fa_cache[key] = emb
    return emb




def compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ── LLM 配置（Ollama qwen2.5:7b）────────────────────────────
_llm = None
_llm_lock = threading.Lock()

_ollama_base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
_ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

def _ollama_generate(prompt: str) -> str:
    try:
        resp = requests.post(
            f"{_ollama_base}/api/generate",
            json={
                "model": _ollama_model,
                "prompt": prompt,
                "stream": False,
                "temperature": OLLAMA_TEMPERATURE,
                "max_tokens": OLLAMA_MAX_TOKENS,
            },
            timeout=60
        )
        return resp.json().get("response", "").strip()
    except Exception as _e:
        logger.warning(f"Ollama API 调用失败: {_e}")
        return ""


# ── LRU 缓存的查询扩展 ─────────────────────────────────────
@lru_cache(maxsize=128)
def _query_expand_cached(query: str) -> tuple:
    """带缓存的 LLM 查询扩展，TTL=DEDUP_WINDOW_HOURS 小时"""
    try:
        return tuple(_query_expand_impl(query))
    except Exception:
        return tuple(_jieba_tokenize(query))


def _query_expand_impl(query: str) -> list[str]:
    """LLM 查询扩展（内部用）"""
    prompt = (
        f"你是一个信息检索助手。用户的问题是：「{query}」\n"
        f"请生成3-5个相关的搜索关键词（用空格分隔，中英文均可）。"
        f"只输出关键词，不要解释，不要标点。"
    )
    try:
        result = _ollama_safe_call(_ollama_generate, prompt)
        if not result:
            return _jieba_tokenize(query)

        tokens = re.findall(r'[\w]{2,}', result)
        return [t for t in tokens if len(t) >= 2][:6]
    except Exception:
        return _jieba_tokenize(query)


def _query_expand(query: str) -> list[str]:
    return list(_query_expand_cached(query))


def _jieba_tokenize(text: str) -> list[str]:
    words = jieba.cut(text)
    return [w for w in words if len(w) >= 2]


# ── 低质量查询识别 ─────────────────────────────────────────
_LOW_QUALITY_PATTERNS = [
    r'^好$', r'^是$', r'^了$', r'^嗯$', r'^啊$',
    r'^现在几点', r'^今天多少号', r'^天气怎么样',
    r'^你好', r'^嗨', r'^在吗',
]


def _is_low_quality_query(query: str) -> bool:
    q = query.strip()
    if len(q) < 2:
        return True
    for pat in _LOW_QUALITY_PATTERNS:
        if re.match(pat, q):
            return True
    return False


# ── 记忆血缘记录 ────────────────────────────────────────────
def _log_lineage(source_id: str, operation: str, reason: str = "",
                 target_id: str = None, details: dict = None) -> None:
    """记录记忆操作到血缘表"""
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            try:
                cur.execute("""
                    INSERT INTO memory_lineage (source_id, operation, reason, target_id, details)
                    VALUES (%s, %s, %s, %s, %s)
                """, (source_id, operation, reason, target_id, json.dumps(details or {})))
                conn.commit()
            finally:
                cur.close()
    except Exception as e:
        logging.warning(f"血缘记录失败: {e}")


# ── 重排：同步完整重排 ─────────────────────────────────────
def _rerank(query: str, candidates: list) -> list[dict]:
    """
    LLM 重排（同步）：
    - 所有候选都分配 rerank_score（LLM 评分或默认 0.5）
    - 候选 <= 5：跳过 LLM，直接用混合分数（降级策略）
    - 候选 > 5：调用 LLM 完整重排
    """
    if len(candidates) <= 1:
        for m in candidates:
            m['rerank_score'] = m.get('score', RERANK_DEFAULT_SCORE)
        return candidates

    # 降级：候选 <= 5，跳过 LLM 调用，直接用混合分数
    if len(candidates) <= RERANK_MIN_CANDIDATES:
        for m in candidates:
            m['rerank_score'] = m.get('score', RERANK_DEFAULT_SCORE)
        return candidates

    # 候选 > 5：同步调用 LLM 重排
    return _rerank_impl(query, candidates)




def temporal_decay_score(created_at, half_life_days: int = DECAY_HALF_LIFE_DAYS) -> float:
    """
    时间衰减系数：按 half_life_days 指数衰减。
    创建时间越久，系数越接近 0（但永不为零）。
    created_at: datetime 对象、ISO 字符串，或 None
    """
    if created_at is None:
        return 1.0
    try:
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        age_days = (datetime.now() - created_at.replace(tzinfo=None)).days
        return 0.5 ** (age_days / half_life_days)
    except Exception:
        return 1.0

def _rerank_impl(query: str, candidates: list) -> list[dict]:
    """LLM 重排实现（同步）"""
    prompt = (
        f"问题：「{query}」\n"
        f"以下是与问题相关的记忆片段，请对每条打分（0-1分），"
        f"衡量它对回答这个问题有多大帮助。\n\n"
    )
    for i, m in enumerate(candidates, 1):
        prompt += f"[{i}] {m['data']}\n"
    prompt += "\n格式：每行一个分数，格式为「序号:分数」（如 1:0.9）\n只输出分数。"

    try:
        result = _ollama_safe_call(_ollama_generate, prompt)
        if not result:
            for m in candidates:
                m['rerank_score'] = m.get('score', RERANK_DEFAULT_SCORE)
            return candidates

        scores_map = {}
        for line in result.strip().split('\n'):
            line = line.strip()
            if ':' in line:
                try:
                    idx, score = line.split(':')
                    scores_map[int(idx.strip())] = float(score.strip())
                except (ValueError, IndexError):
                    pass
        reranked = []
        for i, m in enumerate(candidates, 1):
            m = dict(m)
            m['rerank_score'] = scores_map.get(i, RERANK_DEFAULT_SCORE)
            reranked.append(m)
        reranked.sort(key=lambda x: x['rerank_score'], reverse=True)
        return reranked
    except Exception:
        for m in candidates:
            m['rerank_score'] = m.get('score', RERANK_DEFAULT_SCORE)
        return candidates


# ── 访问热度更新（批量）────────────────────────────────────
def _increment_access(mem_ids: list) -> None:
    """命中的记忆批量 UPDATE（一次提交，高效）"""
    if not mem_ids:
        return
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE mem0
            SET access_count = access_count + 1,
                last_access_time = %s,
                recall_count = recall_count + 1
            WHERE id = ANY(%s::uuid[])
        """, (now, [str(m) for m in mem_ids]))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        cur.close()
        put_conn(conn)


# ── 语义+时效去重 ──────────────────────────────────────────
def cmd_dedup() -> dict:
    """
    语义+时效去重：
    1. pgvector cosine_dist < COSINE_DIST_MERGE（即 sim > 0.85）→ 时效远的直接合并
    2. cosine_dist < COSINE_DIST_NEAR（即 sim > 0.9）→ 时效近的保留较完整的一条
    3. 用 pgvector 的 <=> 算距离，不重复计算 similarity
    """
    with get_conn() as conn:
        cur = None
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, vector, payload, payload->>'created_at' as created_at
            FROM mem0
            WHERE source = 'memory'
            ORDER BY payload->>'created_at' DESC
            LIMIT 200
        """)
        rows = cur.fetchall()
    finally:
        if cur is not None:
            cur.close()

    merged = 0
    skipped = 0
    deleted_ids = set()  # 避免重复删除

    for r in rows:
        old_id, old_vec, old_payload, created_at = r
        if old_id in deleted_ids:
            continue

        old_vec_list = old_vec.tolist() if hasattr(old_vec, 'tolist') else old_vec
        if not old_vec_list:
            continue

        try:
            t_old = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        except Exception:
            t_old = datetime.min.replace(tzinfo=timezone.utc)

        # 找 cosine_dist < COSINE_DIST_MERGE 的相似记忆
        conn2 = get_conn()
        cur2 = conn2.cursor()
        cur2.execute("""
            SELECT id, vector, payload, payload->>'created_at' as created_at,
                   vector <=> %s::vector AS cosine_dist
            FROM mem0
            WHERE id != %s
              AND source = 'memory'
              AND vector <=> %s::vector < COSINE_DIST_MERGE
            ORDER BY vector <=> %s::vector
            LIMIT 5
        """, (old_vec_list, old_id, old_vec_list, old_vec_list))
        candidates = cur2.fetchall()
        cur2.close()
        put_conn(conn2)

        for cand in candidates:
            cand_id, cand_vec, cand_payload, cand_created_at, cos_dist = cand
            if cand_id in deleted_ids:
                continue

            try:
                t_cand = datetime.fromisoformat(cand_created_at.replace('Z', '+00:00'))
            except Exception:
                t_cand = datetime.min.replace(tzinfo=timezone.utc)

            time_diff_hours = abs((t_old - t_cand).total_seconds()) / 3600

            # 决策：合并还是跳过
            # cosine_dist < COSINE_DIST_NEAR（sim > 0.9）→ 几乎相同，保留内容更完整的
            # cosine_dist 0.1-0.15 且时效差 > DEDUP_WINDOW_HOURS 小时 → 合并（不同时间点的近似记录）
            if cos_dist < COSINE_DIST_NEAR:
                # 几乎完全相同，保留内容更完整的
                old_text = old_payload.get('data', '')
                cand_text = cand_payload.get('data', '')
                keep_id = old_id if len(old_text) >= len(cand_text) else cand_id
                del_id = cand_id if keep_id == old_id else old_id
            elif cos_dist < COSINE_DIST_MERGE and time_diff_hours > DEDUP_WINDOW_HOURS:
                # 时间差大但语义近似，保留较新的
                keep_id = old_id if t_old > t_cand else cand_id
                del_id = cand_id if keep_id == old_id else old_id
            else:
                skipped += 1
                continue

            if keep_id == del_id or del_id in deleted_ids:
                continue

            try:
                conn3 = get_conn()
                cur3 = conn3.cursor()
                cur3.execute("DELETE FROM mem0 WHERE id = %s", (str(del_id),))
                conn3.commit()
                cur3.close()
                put_conn(conn3)
                _log_lineage(str(del_id), "merge",
                             reason=f"cos_dist={cos_dist:.3f} time_diff={time_diff_hours:.1f}h, 保留{keep_id}",
                             target_id=keep_id)
                deleted_ids.add(del_id)
                merged += 1
            except Exception as e:
                logger.warning(f"合并失败: {e}")

    return {"merged": merged, "skipped": skipped}


# ── 向量深度去重（子函数）──────────────────────────────────────

def _build_clusters(conn, cur, threshold: float) -> tuple:
    """构建所有记忆的向量相似簇。返回 (clusters: list[set], all_rows)"""
    cur.execute("SELECT id, vector, payload->>'data' FROM mem0")
    all_rows = cur.fetchall()
    print(f"📊 共 {len(all_rows)} 条记忆，开始向量最近邻搜索...")
    clusters, processed = [], set()

    for i, (mid, vec_raw, _) in enumerate(all_rows):
        if mid in processed:
            continue
        vec_str = (vec_raw.tolist() if hasattr(vec_raw, 'tolist')
                   else list(vec_raw) if isinstance(vec_raw, (memoryview, list))
                   else vec_raw)
        try:
            cur.execute("""
                SELECT id, vector <=> %s::vector as dist, payload->>'data' as data
                FROM mem0 WHERE id != %s
                ORDER BY vector <=> %s::vector LIMIT 20
            """, (vec_str, mid, vec_str))
            neighbors = cur.fetchall()
        except psycopg2.Error as e:
            logger.warning(f"向量最近邻查询失败（mid={mid}）: {e}")
            neighbors = []

        cluster = {mid}
        for nid, dist, _ in neighbors:
            if dist < threshold and nid not in processed:
                cluster.add(nid)
        clusters.append(cluster)
        processed.update(cluster)
        if (i + 1) % 200 == 0:
            print(f"  已处理 {i+1}/{len(all_rows)} 条...")
    return clusters, all_rows


def _select_deletions(conn, cur, clusters: list) -> list:
    """每个簇保留最长一条，其余标记删除"""
    to_delete = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        cur.execute("""
            SELECT id, length(payload->>'data') as llen
            FROM mem0 WHERE id = ANY(%s::uuid[])
            ORDER BY length(payload->>'data') DESC
        """, ([str(m) for m in cluster],))
        for j, (cid, _) in enumerate(cur.fetchall()):
            if j > 0:
                to_delete.append(cid)
    return to_delete


def _preview_clusters(conn, cur, clusters: list, shown: int = 5) -> int:
    """打印前N个簇预览，返回含重复的簇数量"""
    dup_groups = 0
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        dup_groups += 1
        if shown <= 0:
            continue
        cur.execute("""
            SELECT id, left(payload->>'data', 60), length(payload->>'data')
            FROM mem0 WHERE id = ANY(%s::uuid[])
            ORDER BY length(payload->>'data') DESC
        """, ([str(m) for m in cluster],))
        members = cur.fetchall()
        print(f"\n  簇（{len(cluster)} 条，保留最长）：")
        for j, (cid, cdata, clen) in enumerate(members):
            mark = " ← 保留" if j == 0 else " ← 删除"
            print(f"    [{cid[:8]}]{mark} ({clen}字) {cdata[:50]}...")
        shown -= 1
    return dup_groups


def cmd_dedup_deep(threshold: float = COSINE_DIST_DEEP, dry_run: bool = True) -> dict:
    """
    基于 pgvector 索引的近似去重（非自连接，O(n·k·log n)）
    threshold: 向量距离阈值，默认 0.1（约=cosine相似度0.9）
    dry_run: True=仅报告，False=执行删除
    """

    conn = None
    cur = None
    try:
        with get_conn() as conn:
            cur = conn.cursor()
        try:
            cur.execute("SELECT id, vector, payload->>'data' FROM mem0")
            all_rows = cur.fetchall()
        except psycopg2.Error as e:
            logger.error(f"加载记忆列表失败: {e}")
            return {"error": str(e)}

        clusters, _ = _build_clusters(conn, cur, threshold)
        to_delete = _select_deletions(conn, cur, clusters)
        dup_groups = sum(1 for c in clusters if len(c) > 1)
        print(f"\n🔍 发现 {len(clusters)} 个簇，其中 {dup_groups} 个含重复")
        print(f"📋 建议删除：{len(to_delete)} 条")

        _preview_clusters(conn, cur, clusters, shown=5)

        if dry_run:
            print(f"\n⚠️ dry_run=True，未执行删除。加 --no-dry-run 执行实际删除")
            return {"clusters": len(clusters), "to_delete": len(to_delete), "dry_run": True}

        if to_delete:
            try:
                cur.executemany(
                    "DELETE FROM mem0 WHERE id = %s",
                    [(mid,) for mid in to_delete]
                )
                conn.commit()
                print(f"\n✅ 已删除 {len(to_delete)} 条重复记忆")
            except psycopg2.Error as e:
                print(f"\n❌ 删除失败（可能是外键依赖）：{e}")
                conn.rollback()
        else:
            print("\n✅ 无重复可删")

        return {"clusters": len(clusters), "deleted": len(to_delete), "dry_run": False}
    finally:
        if cur is not None:
            cur.close()


# ── 访问热度流转 ────────────────────────────────────────────
def cmd_promote_by_heat() -> dict:
    """
    按访问热度将 L1 记忆晋赚到 L2 文件层（带去重）：
    - HOT: access_count 最高的前 10%
    - WARM: 中间 60%
    - COLD: 后 30%
    - 已在 L2 的记忆跳过（payload.promoted_layer 判断）
    - 晋升后在 L1 标记 promoted_layer
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, vector, payload,
               COALESCE(access_count, 0) as ac,
               last_access_time,
               (COALESCE(access_count, 0) + 1) /
                 (EXTRACT(EPOCH FROM (NOW() - COALESCE(last_access_time, NOW()))) / 3600 + 1)
               as heat_score
        FROM mem0
        WHERE source = 'memory'
          AND (payload->>'promoted_layer') IS NULL
          AND last_access_time < NOW() - INTERVAL '1 hour'
        ORDER BY heat_score DESC
        LIMIT 100
    """)
    rows = cur.fetchall()
    total = len(rows)
    cur.close()
    put_conn(conn)

    if total == 0:
        return {"total": 0, "promoted": {"HOT": 0, "WARM": 0, "COLD": 0}, "skipped": 0}

    hot_count = max(1, int(total * HOT_RATIO))
    warm_count = max(1, int(total * WARM_RATIO))

    promoted = {"HOT": 0, "WARM": 0, "COLD": 0}
    skipped = 0

    for i, (mem_id, vec, payload, ac, lat, hs) in enumerate(rows):
        if i < hot_count:
            layer = "HOT"
        elif i < hot_count + warm_count:
            layer = "WARM"
        else:
            layer = "COLD"

        text = payload.get('data', '')
        if not text:
            skipped += 1
            continue
        user = payload.get('user_id', '主人')
        timestamp = payload.get('created_at', datetime.now(timezone.utc).isoformat())

        # 写入 L2 文件（带晋升标记，避免重复追加）
        l2_file = Path(f"{_WORKSPACE_ENV}/memory/{layer}.md")
        # 确保父目录存在（文件不存在时也尝试创建）
        l2_file.parent.mkdir(parents=True, exist_ok=True)
        # 按文本内容去重
        existing_texts = set()
        if l2_file.exists():
            with _locked_open(l2_file, "r", "utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("- ["):
                        m = re.match(r"- \[\d{4}-\d{2}-\d{2}[^\]]*\] \[[^\]]*\] (.+)", line)
                        if m:
                            existing_texts.add(m.group(1))
        if text in existing_texts:
            skipped += 1
        else:
            entry = f"- [{timestamp[:10]}] [{user}] {text}\n"
            with _locked_open(l2_file, "a", "utf-8") as f:
                f.write(entry)
            promoted[layer] += 1

        # 在 L1 标记已晋升（不删除，保留向量检索能力）
        conn2 = get_conn()
        cur2 = conn2.cursor()
        updated_payload = dict(payload)
        updated_payload['promoted_layer'] = layer
        updated_payload['promoted_at'] = datetime.now(timezone.utc).isoformat()
        cur2.execute("""
            UPDATE mem0 SET payload = %s
            WHERE id = %s
        """, (json.dumps(updated_payload), mem_id))
        conn2.commit()
        cur2.close()
        put_conn(conn2)

    return {"total": total, "promoted": promoted, "skipped": skipped}


# ── 每日整理命令 ──────────────────────────────────────────
# ── L2 层记忆文件操作 ────────────────────────────────────────

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


def _write_layer_entries(layer_file: Path, entries: list, now_iso: str):
    """写入层记忆文件（带 header）"""
    header = (
        f"# {layer_file.stem} 层记忆\n"
        f"\n"
        f"_最后更新: {now_iso}_\n"
        f"\n"
    )
    with _locked_open(layer_file, "w", "utf-8") as f:
        f.write(header)
        for e in entries:
            f.write(e + "\n")


def _append_layer_entries_dedup(layer_file: Path, new_lines: list) -> int:
    """
    追加条目到层记忆文件（内容去重）。
    返回实际追加行数。
    """
    if not new_lines:
        return 0
    existing_texts = set()
    if layer_file.exists():
        with _locked_open(layer_file, "r", "utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("- ["):
                    m = re.match(r"- \[\d{4}-\d{2}-\d{2}[^\]]*\] \[[^\]]*\] (.+)", line)
                    if m:
                        existing_texts.add(m.group(1))
    added = 0
    with _locked_open(layer_file, "a", "utf-8") as f:
        for line in new_lines:
            m = re.match(r"- \[\d{4}-\d{2}-\d{2}[^\]]*\] \[[^\]]*\] (.+)", line.strip())
            if m and m.group(1) in existing_texts:
                continue
            f.write(line.strip() + "\n")
            added += 1
    return added


def cmd_organize() -> dict:
    """
    每日整理：
    1. L2 HOT 中超过2天的条目 → 移动到 WARM
    2. L2 WARM 中超过7天的条目 → 移动到 COLD
    3. L2 COLD 归档日志（移动到 archive/）
    4. 检查 L1 与 L2 是否有重复（按 hash）
    5. 输出整理报告
    """
    workspace = Path(_WORKSPACE_ENV)
    memory_dir = workspace / "memory"
    archive_dir = memory_dir / "archive"
    archive_dir.mkdir(exist_ok=True)

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    moved = {"HOT→WARM": 0, "WARM→COLD": 0, "COLD→archive": 0}
    removed = {"HOT": 0, "WARM": 0}

    # ── 整理 HOT → WARM（超过2天） ──────────────────────────────
    hot_file = memory_dir / "HOT.md"
    hot_entries = _read_layer_entries(hot_file)
    still_hot, to_warm = [], []
    for line, ts, text in hot_entries:
        try:
            entry_date = datetime.strptime(ts, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if (now - entry_date).total_seconds() > HOT_MAX_DAYS * 86400:
                to_warm.append(line)
            else:
                still_hot.append(line)
        except Exception:
            still_hot.append(line)

    if to_warm:
        _write_layer_entries(hot_file, still_hot, now_iso)
        warm_file = memory_dir / "WARM.md"
        added = _append_layer_entries_dedup(warm_file, to_warm)
        moved["HOT→WARM"] = added

    # ── 整理 WARM → COLD（超过7天） ─────────────────────────────
    warm_file = memory_dir / "WARM.md"
    warm_entries = _read_layer_entries(warm_file)
    still_warm, to_cold = [], []
    for line, ts, text in warm_entries:
        try:
            entry_date = datetime.strptime(ts, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if (now - entry_date).total_seconds() > WARM_MAX_DAYS * 86400:
                to_cold.append(line)
            else:
                still_warm.append(line)
        except Exception:
            still_warm.append(line)

    if to_cold:
        _write_layer_entries(warm_file, still_warm, now_iso)
        cold_file = memory_dir / "COLD.md"
        added = _append_layer_entries_dedup(cold_file, to_cold)
        moved["WARM→COLD"] = added

    # ── 归档 COLD（移动到 archive/） ─────────────────────────────
    cold_file = memory_dir / "COLD.md"
    if cold_file.exists():
        try:
            archive_file = archive_dir / f"COLD-{now.strftime('%Y-%m')}.md"
            with _locked_open(cold_file, "r", "utf-8") as src:
                content = src.read()
            with _locked_open(archive_file, "a", "utf-8") as dst:
                dst.write(f"\n# ── {now.strftime('%Y-%m-%d')} 归档 ──\n")
                dst.write(content)
            _write_layer_entries(cold_file, [], now_iso)  # 清空
            moved["COLD→archive"] = content.count("- [")
        except Exception as e:
            print(f"⚠️ 归档失败: {e}")

    # ── L1 向量去重 ─────────────────────────────────────────────
    dedup_result = cmd_dedup()

    return {
        "moved": moved,
        "removed": removed,
        "dedup": dedup_result,
        "timestamp": now_iso,
    }


# ── 动态权重计算 ────────────────────────────────────────
def _compute_dynamic_weights(query: str, vec_rows: list, bm25_rows: list) -> tuple:
    """
    根据查询特征和召回结果重叠度动态调整向量/BMA5 权重

    逻辑：
    1. 交集少（互补强）→ 提高 BM25 权重（两者各有价值）
    2. 查询短（<3字）→ 提高向量权重（语义为主）
    3. 查询含专有名词（数字/大写/特殊符）→ 提高 BM25 权重（精确匹配重要）
    4. 无专有名词且查询长 → 默认 70/30

    返回: (vec_weight, bm25_weight)
    """
    vec_ids = {r[0] for r in vec_rows}
    bm25_ids = {r[0] for r in bm25_rows}

    # 1. 交集比例
    if vec_ids and bm25_ids:
        overlap = len(vec_ids & bm25_ids) / len(vec_ids | bm25_ids)
    else:
        overlap = 0.0

    # 2. 查询长度（字符级）
    is_short = len(query.strip()) < 3

    # 3. 专有名词特征：含数字/大写字母/特殊符号
    has_proper_nouns = bool(re.search(r'[0-9A-Z_]', query))

    # 决策逻辑
    if is_short:
        # 短查询靠语义
        vec_w, bm25_w = VEC_W_SHORT, BM25_W_SHORT
    elif has_proper_nouns:
        # 专有名词靠 BM25
        vec_w, bm25_w = VEC_W_PROPER, BM25_W_PROPER
    elif overlap < OVERLAP_LO:
        # 交集少，两者权重平衡（都重要）
        vec_w, bm25_w = VEC_W_OVERLAP_LO, BM25_W_OVERLAP_LO
    else:
        # 交集正常，默认
        vec_w, bm25_w = VEC_W_DEFAULT, BM25_W_DEFAULT

    return vec_w, bm25_w


# ── MMR 多样性去重 ───────────────────────────────────────
def _mmr_dedup(candidates: list, lambda_param: float = 0.5, top_n: int = None) -> list:
    """
    简化 MMR（Max-Marginal-Relevance）：
    在已重排的结果中贪心选择，使结果多样性最大化。
    candidates 中的每条必须包含 'vec' 字段（list[float]，512维）。
    lambda_param: 平衡参数（越大越偏重复盖率，越小越偏多样性）
    """
    if not candidates or len(candidates) <= 1:
        return candidates

    # 先过滤出有向量的候选
    with_vec = [c for c in candidates if c.get("vec")]
    without_vec = [c for c in candidates if not c.get("vec")]

    import math
    selected = []
    remaining = list(with_vec)

    # 按 score 降序排列，先选高分的
    remaining.sort(key=lambda x: x.get("score", 0), reverse=True)

    while remaining:
        best = None
        best_mmr = -999.0
        best_idx = -1

        for i, cand in enumerate(remaining):
            vec = cand.get("vec", [])
            if not vec:
                mmr_score = cand.get("score", 0)  # 无向量候选直接用 score
            else:
                # 计算与已选候选的最大相似度
                max_sim = 0.0
                for s in selected:
                    s_vec = s.get("vec", [])
                    if not s_vec:
                        continue
                    dot = sum(a*b for a, b in zip(vec, s_vec))
                    norm_c = math.sqrt(sum(x*x for x in vec))
                    norm_s = math.sqrt(sum(x*x for x in s_vec))
                    sim = dot / (norm_c * norm_s + 1e-9)
                    max_sim = max(max_sim, sim)

                # MMR = lambda * rel - (1-lambda) * max_sim
                rel = cand.get("score", 0)
                mmr_score = lambda_param * rel - (1 - lambda_param) * max_sim

            if mmr_score > best_mmr:
                best_mmr = mmr_score
                best = cand
                best_idx = i

        if best is None:
            break

        selected.append(best)
        remaining.pop(best_idx)

        if top_n and len(selected) >= top_n:
            break

    # 无向量候选补充到末尾（最多补充 top_n - len(selected) 个）
    if top_n:
        slots_left = top_n - len(selected)
        if slots_left > 0 and without_vec:
            without_vec.sort(key=lambda x: x.get("score", 0), reverse=True)
            selected.extend(without_vec[:slots_left])

    return selected


# ── 主搜索命令 ─────────────────────────────────────────────

# ── 检索辅助函数 ────────────────────────────────────────────

def _vector_search(cur, vec, top_k: int, user_id: str = None) -> list:
    """向量语义检索，返回 (id, vector, vec_dist, payload) 元组列表"""
    if user_id:
        cur.execute("""
            SELECT id, vector, vector <=> %s::vector AS vec_dist, payload
            FROM mem0
            WHERE payload->>'user_id' = %s
            ORDER BY vector <=> %s::vector
            LIMIT %s
        """, (vec, user_id, vec, top_k))
    else:
        cur.execute("""
            SELECT id, vector, vector <=> %s::vector AS vec_dist, payload
            FROM mem0
            ORDER BY vector <=> %s::vector
            LIMIT %s
        """, (vec, vec, top_k))
    return cur.fetchall()


def _escape_like(text: str) -> str:
    """转义 LIKE/ILIKE 中的通配符 %、_、\\"""
    return re.sub(r"([%_\\])", r"\\\1", text)


def _bm25_search(cur, tsquery_str: str, jieba_terms: list, top_k: int,
                 user_id: str = None) -> list:
    """
    BM25 全文检索 + ILIKE 手动匹配（pg_trgm GIN 索引已建，ILIKE 走索引扫描）。
    返回 (id, bm25_rank, payload) 元组列表。
    """
    rows = []
    if tsquery_str:
        if user_id:
            cur.execute("""
                SELECT id, GREATEST(ts_rank_cd(fts, query), 0.001) AS bm25_rank, payload
                FROM mem0, to_tsquery('chinese_zh', %s) query
                WHERE fts @@ query AND payload->>'user_id' = %s
                ORDER BY bm25_rank DESC
                LIMIT %s
            """, (tsquery_str, user_id, top_k))
        else:
            cur.execute("""
                SELECT id, GREATEST(ts_rank_cd(fts, query), 0.001) AS bm25_rank, payload
                FROM mem0, to_tsquery('chinese_zh', %s) query
                WHERE fts @@ query
                ORDER BY bm25_rank DESC
                LIMIT %s
            """, (tsquery_str, top_k))
        rows = cur.fetchall()

    if jieba_terms:
        escaped = [_escape_like(w) for w in jieba_terms]
        like_values = [f'%{w}%' for w in escaped]
        like_conditions = ' OR '.join([f"payload->>'data' ILIKE %s" for _ in escaped])
        if user_id:
            cur.execute(f"""
                SELECT id, 0.3 AS bm25_rank, payload
                FROM mem0
                WHERE ({like_conditions}) AND payload->>'user_id' = %s
                LIMIT %s
            """, like_values + [user_id, top_k])
        else:
            cur.execute(f"""
                SELECT id, 0.3 AS bm25_rank, payload
                FROM mem0
                WHERE {like_conditions}
                LIMIT %s
            """, like_values + [top_k])
        ilike_rows = cur.fetchall()
        seen = {r[0] for r in rows}
        rows += [r for r in ilike_rows if r[0] not in seen]
    return rows


def cmd_search(query: str, top_k: int = 5, use_rerank: bool = True, user_id: str = None, use_expand: bool = True) -> list[dict]:
    """
    增强搜索：
    1. 低质量查询直接返回空
    2. LLM 查询扩展（带缓存）
    3. jieba + 向量 + BM25 动态权重混合检索
    4. 重排降级（<=5跳过）+ 异步重排
    5. 访问热度更新
    """
    # 低质量查询快速返回
    if _is_low_quality_query(query):
        return []

    # LLM 查询扩展（带缓存）
    expanded_terms = _query_expand(query) if use_expand else []
    raw_terms = [t for t in re.findall(r'[\w]{2,}', query) if len(t) >= 2]
    all_terms = list(dict.fromkeys(raw_terms + expanded_terms))

    vec = generate_vector(query)
    conn = None
    cur = None
    try:
        with get_conn() as conn:
            cur = conn.cursor()

        # 向量搜索
        vec_rows = _vector_search(cur, vec, top_k, user_id=user_id)

        # BM25 + ILIKE 搜索
        jieba_terms = _jieba_tokenize(query)
        all_terms_str = list(dict.fromkeys(jieba_terms + all_terms))
        tsquery_str = ' & '.join(all_terms_str) if all_terms_str else ''
        bm25_rows = _bm25_search(cur, tsquery_str, jieba_terms, top_k, user_id=user_id)
    finally:
        if cur is not None:
            cur.close()


    # 动态权重
    vec_w, bm25_w = _compute_dynamic_weights(query, vec_rows, bm25_rows)

    # 混合排序
    scores = {}
    vec_max = max((float(r[2]) for r in vec_rows), default=1)  # r[2]=vec_dist
    bm25_max = max((float(r[1]) for r in bm25_rows), default=1)  # r[1]=bm25_rank（不变）

    for mem_id, vec, vec_dist, payload in vec_rows:  # 4列: id, vector, vec_dist, payload
        vec_sim = 1 - float(vec_dist)  # cosine distance: 0=相同,1=正交,2=相反 → similarity = 1 - dist
        created_at = payload.get("created_at") if isinstance(payload, dict) else None
        decay = temporal_decay_score(created_at, half_life_days=DECAY_HALF_LIFE_DAYS)
        scores[mem_id] = scores.get(mem_id, 0) + vec_w * vec_sim * decay

    for mem_id, bm25_rank, payload in bm25_rows:
        bm25_score = float(bm25_rank) / bm25_max if bm25_max > 0 else 0
        created_at = payload.get("created_at") if isinstance(payload, dict) else None
        decay = temporal_decay_score(created_at, half_life_days=DECAY_HALF_LIFE_DAYS)
        scores[mem_id] = scores.get(mem_id, 0) + bm25_w * bm25_score * decay

    # 多取候选用于重排（重排后才能准确排序）
    ranked_ids = sorted(scores.keys(), key=lambda i: scores[i], reverse=True)[:top_k * 3]

    # id_to_payload: mem_id -> payload（从 vec_rows 的 payload 列，和 bm25_rows 的 payload 列）
    # vec_rows 现在是 (id, vector, vec_dist, payload) — 4列
    # bm25_rows 是 (id, bm25_rank, payload) — 3列
    id_to_payload = {r[0]: r[3] for r in vec_rows}
    id_to_payload.update({r[0]: r[2] for r in bm25_rows})

    # id_to_vec: mem_id -> vector list（从 vec_rows 获取，供 MMR 使用）
    id_to_vec = {}
    for r in vec_rows:
        vec_val = r[1]  # vector 列
        if isinstance(vec_val, (list, tuple)):
            id_to_vec[r[0]] = list(vec_val)
        elif isinstance(vec_val, str):
            import json
            try:
                id_to_vec[r[0]] = json.loads(vec_val)
            except Exception:
                id_to_vec[r[0]] = []
        else:
            id_to_vec[r[0]] = []

    candidates = []
    for mem_id in ranked_ids:
        payload = id_to_payload.get(mem_id, {})
        candidates.append({
            "id": mem_id,
            "score": round(scores[mem_id], 4),
            "vec": id_to_vec.get(mem_id, []),
            "data": payload.get("data", ""),
            "user_id": payload.get("user_id", ""),
            "created_at": payload.get("created_at", ""),
        })

    # 重排（同步，候选不足 max(5, top_k*2) 时降级）
    if use_rerank and len(candidates) >= max(5, top_k * 2):
        candidates = _rerank(query, candidates)

    # 访问热度更新（取 top_k 而非全部）
    if candidates:
        threading.Thread(target=_increment_access, args=([c['id'] for c in candidates[:top_k]],), daemon=True).start()

    # MMR 多样性去重（在重排之后，防止相似结果堆叠）
    if len(candidates) > 3:
        candidates = _mmr_dedup(candidates, lambda_param=0.5, top_n=top_k)

    # 返回 top_k
    return candidates[:top_k]


def cmd_add(text: str, user: str = "主人", force: bool = False) -> str:
    """写入单条记忆，带内容质量门"""
    from datetime import timedelta

    content = text.strip()
    if not content:
        raise ValueError("内容为空，拒绝写入")

    # ── 内容质量门 ───────────────────────────────────────────────
    if not force:
        # ① 过短内容（< 15 字符）
        if len(content) < MIN_CONTENT_CHARS:
            raise ValueError(f"内容过短（{len(content)}字符<15），用 --force 强制写入")

        # ② 无意义完整单句（精确匹配，非子串）
        trivial_phrases = {
            "好的", "嗯", "知道了", "ok", "嗯嗯", "好的好的",
            "收到", "看完了", "了解", "okay", "好的。", "嗯嗯嗯"
        }
        if content in trivial_phrases:
            raise ValueError("无意义单句，用 --force 强制写入")

        # ③ DEDUP_WINDOW_HOURS 小时内重复写入（统一用 ISO 字符串比较）
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=DEDUP_WINDOW_HOURS)).isoformat()
        conn_check = get_conn()
        cur_check = conn_check.cursor()
        cur_check.execute("""
            SELECT id FROM mem0
            WHERE payload->>'user_id' = %s
              AND payload->>'data' = %s
              AND payload->>'created_at' > %s
            LIMIT 1
        """, (user, content, one_hour_ago))
        dup = cur_check.fetchone()
        cur_check.close()
        put_conn(conn_check)
        if dup:
            raise ValueError(f"DEDUP_WINDOW_HOURS 小时内已存在（id={dup[0][:8]}），用 --force 强制写入")
    # ── 质量门结束 ───────────────────────────────────────────────

    vec = generate_vector(content)
    now = datetime.now(timezone.utc).isoformat()
    mem_id = str(uuid.uuid4())
    payload = {
        "data": content,
        "hash": compute_hash(content),
        "role": "user",
        "user_id": user,
        "created_at": now,
        "updated_at": now,
    }

    with get_conn() as conn:
        cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO mem0 (id, vector, payload) VALUES (%s, %s, %s)",
            (mem_id, vec, json.dumps(payload)),
        )
        conn.commit()
    finally:
        if cur is not None:
            cur.close()
    return mem_id


def cmd_stats() -> dict:
    with get_conn() as conn:
        cur = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM mem0")
        count = cur.fetchone()[0]
        cur.execute("SELECT payload->>'user_id' AS u, COUNT(*) FROM mem0 GROUP BY u")
        by_user = dict(cur.fetchall())
        # recall 统计
        cur.execute("SELECT SUM(recall_count), MAX(recall_count) FROM mem0 WHERE recall_count > 0")
        recall_row = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM mem0 WHERE recall_count > 0")
        recall_hit = cur.fetchone()[0]
    finally:
        if cur is not None:
            cur.close()
    return {
        "total": count,
        "by_user": by_user,
        "recall_total": recall_row[0] or 0,
        "recall_max": recall_row[1] or 0,
        "recall_hit_count": recall_hit,
    }


def cmd_delete(mem_id: str) -> bool:
    _log_lineage(mem_id, "delete", reason="手动删除")
    with get_conn() as conn:
        cur = None
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM mem0 WHERE id = %s", (mem_id,))
        deleted = cur.rowcount > 0
        conn.commit()
    finally:
        if cur is not None:
            cur.close()
    return deleted


def cmd_list(limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        cur = None
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, payload->>'data' AS data,
                   payload->>'user_id' AS user_id,
                   payload->>'created_at' AS created_at
            FROM mem0
            ORDER BY payload->>'created_at' DESC
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
    finally:
        if cur is not None:
            cur.close()
    return [{"id": r[0], "data": r[1] or "", "user_id": r[2] or "", "created_at": r[3] or ""} for r in rows]


def cmd_summarize(query: str, top_k: int = 5) -> dict:
    memories = cmd_search(query, top_k)
    if not memories:
        return {"query": query, "summary": "未找到相关记忆", "memories": []}

    context_parts = [f"[{i+1}] {m['data']}" for i, m in enumerate(memories)]
    context = "\n".join(context_parts)

    prompt = (
        f"根据以下记忆片段，回答问题「{query}」。"
        f"如果记忆内容不足以回答，请如实说明。"
        f"\n\n=== 记忆 ===\n{context}\n\n"
        f"=== 问题 ===\n{query}\n\n"
        f"=== 回答 ==="
    )

    try:
        result = _ollama_safe_call(_ollama_generate, prompt)
    except Exception:
        result = "（LLM 不可用）"

    return {"query": query, "summary": result, "memories": memories}


def cmd_recall_analysis(limit: int = 50) -> None:
    """分析 recall=0 的低价值记忆，输出内容供人工判断"""
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute("""
            SELECT id, payload->>'data' as data,
                   payload->>'user_id' as user_id,
                   payload->>'created_at' as created_at,
                   recall_count
            FROM mem0
            WHERE recall_count = 0
            ORDER BY (payload->>'created_at')::timestamp DESC
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()

        cur.execute("SELECT count(*) FROM mem0 WHERE recall_count = 0")
        total_zero = cur.fetchone()[0]

    short = short_mid = trivial = good = 0
    trivial_phrases = {
        "好的", "嗯", "知道了", "ok", "嗯嗯", "好的好的",
        "收到", "看完了", "了解", "okay", "好的。", "嗯嗯嗯"
    }

    print(f"📊 recall=0 记忆分析（共 {total_zero} 条，显示最新 {limit} 条）\n")

    for r in rows:
        mid, data, user_id, created_at, rc = r
        content = (data or "").strip()
        words = len(content)

        if content in trivial_phrases:
            flag, cat = "🔴 无意义", "trivial"
        elif words < MIN_CONTENT_CHARS:
            flag, cat = "🔴 过短", "short"
        elif words < SHORT_CONTENT_CHARS:
            flag, cat = "🟡 较短", "short_mid"
        else:
            flag, cat = "🟢 可能有价值", "good"

        if cat == "trivial":
            trivial += 1
        elif cat == "short":
            short += 1
        elif cat == "short_mid":
            short_mid += 1
        elif cat == "good":
            good += 1
        try:
            ts = datetime.fromisoformat(created_at.replace('Z', '+00:00')).strftime("%m-%d %H:%M")
        except Exception:
            ts = created_at[:16] if created_at else "未知"
        preview = content[:55] + ("..." if len(content) > 55 else "")
        print(f"{flag} [{ts}] {preview}")

    print(f"\n📈 统计（{limit}条样本）：")
    print(f"   🔴 无意义：{trivial} 条")
    print(f"   🔴 过短(<15字)：{short} 条")
    print(f"   🟡 较短(15-30字)：{short_mid} 条")
    print(f"   🟢 可能有价值：{good} 条")
    print(f"\n💡 建议：")
    if short + trivial > 0:
        print(f"   · 删除 {short+trivial} 条过短/无意义记忆")
    if good > short:
        print(f"   · {good} 条可能价值，需人工复核")
    print(f"   · 运行 dedup-deep 批量处理重复")


def cmd_session_l1_gap(gap_threshold: float = 0.3,
                       overlap_threshold: float = COSINE_DIST_DEEP,
                       limit: int = 500) -> None:
    """找出 session 片段中有但 L1 记忆中无相似内容的条目"""
    import json
    from pathlib import Path

    conn = None
    cur = None
    try:
        with get_conn() as conn:
            cur = conn.cursor()

        # 尝试多个可能的 session 索引路径
        workspace = Path(_WORKSPACE_ENV)
        idx_paths = [
            workspace / ".sessions" / "index.jsonl",
            workspace / "sessions" / "index.jsonl",
            Path.home() / ".openclaw" / "sessions" / "index.jsonl",
        ]

        session_fragments = []
        for idx_path in idx_paths:
            if idx_path.exists():
                with open(idx_path) as f:
                    for line in f:
                        try:
                            obj = json.loads(line)
                            if obj.get("text"):
                                session_fragments.append(obj["text"][:200])
                        except Exception:
                            pass
                break

        if not session_fragments:
            try:
                cur.execute("""
                    SELECT left(content, 200)
                    FROM session_messages
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (limit,))
                session_fragments = [r[0] for r in cur.fetchall() if r[0]]
            except psycopg2.Error:
                print("⚠️ 未找到 session 索引文件，且 session_messages 表不存在")
                print("   提示：session 索引由 session_indexer.py 生成，需先运行索引任务")
                cur.close()
                return

        print(f"📊 Session 片段：{len(session_fragments)} 条")
        cur.execute("SELECT count(*) FROM mem0")
        l1_total = cur.fetchone()[0]
        print(f"📊 L1 记忆总数：{l1_total} 条\n")

        if not session_fragments:
            print("❌ 无 session 片段可分析")
            cur.close()
            put_conn(conn)
            return

        gap_fragments = []
        overlap_fragments = []
        failed = 0

        print(f"🔍 开始分析（gap>{gap_threshold}, overlap<{overlap_threshold}）...")

        for i, fragment in enumerate(session_fragments):
            if i > 0 and i % 50 == 0:
                print(f"  进度 {i}/{len(session_fragments)}...")

            try:
                vec = generate_vector(fragment)
            except Exception:
                failed += 1
                continue

            try:
                cur.execute("""
                    SELECT id, vector <=> %s::vector as dist,
                           left(payload->>'data', 80)
                    FROM mem0
                    ORDER BY vector <=> %s::vector
                    LIMIT 1
                """, (vec, vec))
                row = cur.fetchone()
            except psycopg2.Error:
                failed += 1
                continue

            if row:
                min_dist = row[1]
                if min_dist > gap_threshold:
                    gap_fragments.append((fragment[:80], round(min_dist, 3)))
                elif min_dist < overlap_threshold:
                    overlap_fragments.append((fragment[:80], round(min_dist, 3)))

        if failed:
            print(f"⚠️ {failed} 个片段向量生成/检索失败，已跳过")

        mid_zone = len(session_fragments) - len(gap_fragments) - len(overlap_fragments) - failed

        print(f"\n📈 分析结果（{len(session_fragments)} 条片段）：")
        print(f"   🟢 重叠（已覆盖）：{len(overlap_fragments)} 条")
        print(f"   🔴 缺口（未覆盖）：{len(gap_fragments)} 条")
        print(f"   ⬜ 中间地带：{mid_zone} 条")
        if failed:
            print(f"   ⚠️ 生成失败：{failed} 条")

        if gap_fragments:
            print(f"\n🔴 Gap 样本（前 10 条，建议审查后写入 L1）：")
            for frag, dist in gap_fragments[:10]:
                print(f"   dist={dist} | {frag[:60]}...")

        if overlap_fragments:
            print(f"\n🟢 Overlap 样本（前 5 条，已重复）：")
            for frag, dist in overlap_fragments[:5]:
                print(f"   dist={dist} | {frag[:60]}...")

        print(f"\n💡 建议：")
        if len(gap_fragments) > 10:
            print(f"   · {len(gap_fragments)} 条 session 内容在 L1 无相似，建议审查后写入")
        else:
            print(f"   · Session 与 L1 重叠度良好")

    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            put_conn(conn)


# ── 血缘表清理 ─────────────────────────────────────────────
def cmd_lineage_cleanup(days: int = 90, dry_run: bool = True) -> dict:
    """
    清理 memory_lineage 表中 N 天前的记录，防止无限增长
    days: 保留天数，默认90天
    dry_run: True=仅报告，False=执行删除
    """
    conn = None
    cur = None
    try:
        with get_conn() as conn:
            cur = conn.cursor()

        cur.execute("SELECT count(*) FROM memory_lineage")
        total = cur.fetchone()[0]

        cur.execute("""
            SELECT count(*) FROM memory_lineage
            WHERE created_at <= NOW() - INTERVAL '%s days'
        """, (days,))
        old_count = cur.fetchone()[0]

        print(f"📊 memory_lineage 统计：")
        print(f"   总记录：{total} 条")
        print(f"   即将删除（{days}天前）：{old_count} 条")

        if dry_run:
            print(f"\n⚠️ dry_run=True，未执行删除。加 --no-dry-run 执行实际删除")
            return {"total": total, "old": old_count, "deleted": 0, "dry_run": True}

        if old_count > 0:
            cur.execute("""
                DELETE FROM memory_lineage
                WHERE created_at <= NOW() - INTERVAL '%s days'
            """, (days,))
            conn.commit()
            print(f"\n✅ 已删除 {old_count} 条过期血缘记录")
        else:
            print(f"\n✅ 无过期记录需要清理")

        return {"total": total, "old": old_count, "deleted": old_count, "dry_run": False}
    finally:
        if cur is not None:
            cur.close()



# ── CLI ───────────────────────────────────────────────────
def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="L1 Memory Layer CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="添加记忆")
    p_add.add_argument("text", help="记忆文本")
    p_add.add_argument("--user", default="主人", help="所属用户")
    p_add.add_argument("--force", action="store_true", help="强制写入（跳过质量门）")

    p_search = sub.add_parser("search", help="语义搜索")
    p_search.add_argument("query", help="查询文本")
    p_search.add_argument("--top-k", type=int, default=5)
    p_search.add_argument("--user", default=None, help="过滤用户")
    p_search.add_argument("--json", action="store_true", help="输出JSON格式（便于程序解析）")

    p_stats = sub.add_parser("stats", help="统计信息")

    p_del = sub.add_parser("delete", help="删除记忆")
    p_del.add_argument("id", help="记忆 ID")

    p_list = sub.add_parser("list", help="列出记忆")
    p_list.add_argument("--limit", type=int, default=20)

    p_sum = sub.add_parser("summarize", help="检索记忆并 LLM 摘要")
    p_sum.add_argument("query", help="查询/问题")
    p_sum.add_argument("--top-k", type=int, default=5)

    p_dedup = sub.add_parser("dedup", help="语义+时效去重")

    p_dedup_deep = sub.add_parser("dedup-deep", help="基于向量索引的深度去重")
    p_dedup_deep.add_argument("--threshold", type=float, default=0.1, help="向量距离阈值，默认0.1")
    p_dedup_deep.add_argument("--no-dry-run", action="store_true", help="实际执行删除（默认dry-run）")

    p_recall = sub.add_parser("recall-analysis", help="分析从未召回的沉默记忆")
    p_recall.add_argument("--limit", type=int, default=50, help="显示最新N条，默认50")

    p_gap = sub.add_parser("session-gap", help="分析session索引与L1的覆盖缺口")
    p_gap.add_argument("--gap-threshold", type=float, default=0.3, help="gap阈值，默认0.3")
    p_gap.add_argument("--overlap-threshold", type=float, default=0.1, help="overlap阈值，默认0.1")
    p_gap.add_argument("--limit", type=int, default=500, help="最多处理session片段数")

    p_heat = sub.add_parser("promote-heat", help="按访问热度流转（加晋升标记）")
    p_org = sub.add_parser("organize", help="每日整理（HOT→WARM→COLD流转 + 去重）")

    p_lineage = sub.add_parser("lineage-cleanup", help="清理 memory_lineage 表过期记录")
    p_lineage.add_argument("--days", type=int, default=90, help="保留天数，默认90天")
    p_lineage.add_argument("--no-dry-run", action="store_true", help="实际执行删除（默认dry-run）")

    p_serve = sub.add_parser("serve", help="启动 HTTP API 服务（供 OpenClaw 插件调用）")
    p_serve.add_argument("--port", type=int, default=18793, help="监听端口，默认18793")
    p_serve.add_argument("--host", default="127.0.0.1", help="监听地址，默认127.0.0.1")

    args = parser.parse_args()

    try:
        if args.cmd == "add":
            mid = cmd_add(args.text, user=args.user, force=args.force)
            print(f"✅ 已添加记忆: {mid}")

        elif args.cmd == "search":
            results = cmd_search(args.query, args.top_k, user_id=args.user)
            if args.json:
                output = []
                for r in results:
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
            print(f"热度流转完成：共处理 {r['total']} 条，已晋崑 {sum(r['promoted'].values())} 条")
            print(f"  跳过（已晋崑）: {r.get('skipped', 0)} 条")
            for layer, cnt in r['promoted'].items():
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

    except Exception as e:
        print(f"❌ 错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
