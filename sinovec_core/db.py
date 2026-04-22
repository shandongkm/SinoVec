# ── SinoVec 数据库和文件锁模块 ───────────────────────────────────────
"""
DB 连接池、文件锁等基础设施。
"""
import fcntl
import logging
import sys
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Union

from psycopg2.pool import SimpleConnectionPool

from common import DB_CONFIG

logger = logging.getLogger(__name__)

# ── 连接池（线程安全）────────────────────────────────────────────
_db_pool: SimpleConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> SimpleConnectionPool:
    global _db_pool
    if _db_pool is None:
        with _pool_lock:
            if _db_pool is None:
                _db_pool = SimpleConnectionPool(1, 20, **DB_CONFIG)
                logger.info("DB 连接池已初始化（1-20连接）")
                try:
                    _warm = _db_pool.getconn()
                    _warm.cursor().execute("SELECT 1")
                    _warm.cursor().close()
                    _db_pool.putconn(_warm)
                    logger.info("DB 连接池预热完成")
                except Exception as e:
                    logger.warning(f"连接池预热失败: {e}")
    return _db_pool


@contextmanager
def get_conn():
    """从连接池获取连接，自动归还（防止连接泄漏）"""
    conn = _get_pool().getconn()
    try:
        yield conn
    finally:
        _get_pool().putconn(conn)


# ── 运行时检测 fts 列使用的分词配置 ─────────────────────────────────
def _detect_ts_config() -> str:
    """
    运行时检测 sinovec.fts 列实际使用的文本搜索配置名。
    检测失败时默认返回 'simple'（保守降级）。
    仅在模块加载时执行一次，后续使用全局常量 TS_CONFIG。
    """
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT pg_get_expr(adbin, adrelid)
                FROM pg_attrdef
                WHERE adrelid = 'sinovec'::regclass
                  AND adnum = (
                      SELECT attnum FROM pg_attribute
                      WHERE attrelid = 'sinovec'::regclass
                        AND attname = 'fts'
                  )
            """)
            row = cur.fetchone()
            cur.close()
            if row and row[0]:
                expr = str(row[0])
                if 'chinese_zh' in expr:
                    logger.info("检测到 fts 列使用 chinese_zh 分词配置")
                    return "chinese_zh"
            logger.warning("fts 列未找到或配置未知，使用 simple 分词")
    except Exception as e:
        logger.warning(f"TS 配置检测失败，使用 simple: {e}")
    return "simple"


TS_CONFIG = _detect_ts_config()

if TS_CONFIG == "simple":
    logger.warning(
        "fts 列使用 simple 分词配置（无 zhparser）。"
        "如已安装 zhparser 请运行 fix-zhparser.sh 后执行: "
        "sudo systemctl restart memory-sinovec"
    )
    print(
        "WARNING: fts 列使用 simple 分词配置（无 zhparser）。"
        "如已安装 zhparser 请运行 fix-zhparser.sh 后执行: "
        "sudo systemctl restart memory-sinovec",
        file=sys.stderr,
    )
else:
    logger.info(f"fts 列使用 {TS_CONFIG} 分词配置")


# ── 文件操作加锁 ───────────────────────────────────────────────
class _LockedFile:
    """带锁的文件对象，退出时释放锁并关闭"""

    __slots__ = ("_f",)

    def __init__(self, f) -> None:
        self._f = f

    def __enter__(self):
        return self._f

    def __exit__(self, *args):
        try:
            fcntl.flock(self._f.fileno(), fcntl.LOCK_UN)
        finally:
            self._f.close()


def _locked_open(
    path: str,
    mode: str = "r",
    encoding: str = "utf-8",
) -> Union[_LockedFile, object]:
    """
    文件操作加锁上下文管理器。
    - 写模式（w/a/w+/a+）：独占锁 LOCK_EX
    - 读模式（r/rb）：共享锁 LOCK_SH
    - 其他模式：不加锁，直接返回普通文件对象
    """
    if mode in ("w", "a", "w+", "a+"):
        lock_type = fcntl.LOCK_EX
    elif mode in ("r", "rb"):
        lock_type = fcntl.LOCK_SH
    else:
        return open(path, mode, encoding=encoding)
    f = open(path, mode, encoding=encoding)
    fcntl.flock(f.fileno(), lock_type)
    return _LockedFile(f)
