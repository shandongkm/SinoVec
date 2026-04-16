"""
common.py - SinoVec 公共模块
DB 配置、连接池、FastEmbed embedding 逻辑统一入口
"""
import os
import threading
import logging
from contextlib import contextmanager
from psycopg2.pool import SimpleConnectionPool

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============ DB 配置 ============
_db_pass = os.getenv("MEMORY_DB_PASS", "")
if not _db_pass:
    raise RuntimeError("MEMORY_DB_PASS environment variable is not set.")

DB_CONFIG = {
    "host": os.getenv("MEMORY_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("MEMORY_DB_PORT", "5433")),
    "database": os.getenv("MEMORY_DB_NAME", "memory"),
    "user": os.getenv("MEMORY_DB_USER", "sinovec"),
    "password": _db_pass,
}

# ============ 连接池（线程安全） ============
_db_pool = None
_pool_lock = threading.Lock()

def _get_pool():
    global _db_pool
    if _db_pool is None:
        with _pool_lock:
            if _db_pool is None:
                _db_pool = SimpleConnectionPool(1, 20, **DB_CONFIG)
    return _db_pool

@contextmanager
def get_conn():
    """线程安全的数据库连接上下文管理器"""
    conn = _get_pool().getconn()
    try:
        yield conn
    finally:
        _get_pool().putconn(conn)

# ============ FastEmbed Embedding（512维） ============
_embedding_model = None
_embedding_lock = threading.Lock()

def get_embedding(text: str) -> list:
    """使用 FastEmbed BAAI/bge-small-zh-v1.5 生成 512 维向量"""
    global _embedding_model
    if _embedding_model is None:
        with _embedding_lock:
            if _embedding_model is None:
                hf_proxy = os.getenv("HF_HUB_PROXY", "")
                if hf_proxy:
                    os.environ["HF_HUB_PROXY"] = hf_proxy
                from fastembed import TextEmbedding
                cache_dir = os.environ.get("FASTEMBED_CACHE_DIR",
                                          os.path.expanduser("~/.cache/fastembed"))
                _embedding_model = TextEmbedding("BAAI/bge-small-zh-v1.5",
                                                  cache_dir=cache_dir)
    arr = list(_embedding_model.embed([text]))[0]
    return [float(x) for x in arr]
