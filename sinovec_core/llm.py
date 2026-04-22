# ── SinoVec LLM / Ollama 模块 ───────────────────────────────────────
"""
Ollama LLM 调用封装：
  - 向量生成（FastEmbed）
  - 查询扩展（将短查询展开为多个相关关键词）
  - LLM 重排（对候选结果二次打分）
  - 时间衰减系数
  - 血缘记录
"""
import hashlib
import logging
import os
import re
import threading
from datetime import datetime, timezone
from typing import Optional

from cachetools import TTLCache

from sinovec_core.constants import (
    OLLAMA_TEMPERATURE, OLLAMA_MAX_TOKENS,
    DECAY_HALF_LIFE_DAYS, RERANK_MIN_CANDIDATES,
    RERANK_DEFAULT_SCORE, TOP_K_RERANK, QUERY_EXPANSION_MAX,
    MIN_QUERY_TERM_LEN,
)

logger = logging.getLogger(__name__)

# ── Ollama 配置（从环境变量读取，保持灵活性）───────────────────────────
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

# ── FastEmbed 向量模型（延迟加载，线程安全）────────────────────────────
_embedding_model: Optional[object] = None
_embedding_lock = threading.Lock()


def _get_embedding_model() -> object:
    """延迟加载 FastEmbed 模型（线程安全，单例）"""
    global _embedding_model
    if _embedding_model is None:
        with _embedding_lock:
            if _embedding_model is None:
                hf_proxy = os.getenv("HF_HUB_PROXY", "")
                if hf_proxy:
                    os.environ["HF_HUB_PROXY"] = hf_proxy
                from fastembed import TextEmbedding
                cache_dir = os.environ.get(
                    "FASTEMBED_CACHE_DIR",
                    os.path.expanduser("~/.cache/fastembed"),
                )
                _embedding_model = TextEmbedding("BAAI/bge-small-zh-v1.5", cache_dir=cache_dir)
    return _embedding_model


def generate_vector(text: str) -> list[float]:
    """使用 FastEmbed BAAI/bge-small-zh-v1.5 生成 512 维向量"""
    model = _get_embedding_model()
    arr = list(model.embed([text]))[0]
    return [float(x) for x in arr]


def compute_hash(text: str) -> str:
    """MD5 哈希（用于去重检测）"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


# ── Ollama 可用性检测 ───────────────────────────────────────────────
def _ollama_check_available() -> bool:
    """检测 Ollama 服务是否可用"""
    try:
        import requests
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _ollama_model_exists(model: str) -> bool:
    """检测指定模型是否已拉取"""
    try:
        import requests
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
        if r.status_code != 200:
            return False
        names = [m.get("name", "") for m in r.json().get("models", [])]
        return any(model in n for n in names)
    except Exception:
        return False


def _ollama_generate(prompt: str, model: Optional[str] = None) -> str:
    """调用 Ollama 生成文本（超时 30s，失败返回空字符串）"""
    import requests
    model = model or OLLAMA_MODEL
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "temperature": OLLAMA_TEMPERATURE,
                "options": {"num_predict": OLLAMA_MAX_TOKENS},
            },
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json().get("response", "").strip()
    except requests.Timeout:
        logger.warning(f"Ollama 生成超时（{model}）")
    except Exception as e:
        logger.debug(f"Ollama 生成异常: {e}")
    return ""


# ── 查询扩展 ────────────────────────────────────────────────────────
_query_expand_cache = TTLCache(maxsize=200, ttl=3600)


def _query_expand_impl(query: str) -> list[str]:
    """LLM 查询扩展：将短查询展开为多个相关关键词（提升召回率）"""
    if not _ollama_check_available():
        return []
    if len(query) < MIN_QUERY_TERM_LEN:
        return []
    prompt = (
        f"请为以下查询生成 {QUERY_EXPANSION_MAX} 个相关搜索关键词，"
        f"用空格分隔，直接输出关键词不要解释：\n{query}"
    )
    raw = _ollama_generate(prompt)
    if not raw:
        return []
    # R3 修复：兼容中文标点（顿号、逗号）分隔，而不仅是空格
    terms = re.split(r'[,，、\s]+', raw)
    return [t.strip() for t in terms if t.strip()][:QUERY_EXPANSION_MAX]


def _query_expand_cached(query: str) -> tuple[list[str], bool]:
    """查询扩展缓存包装：返回 (扩展词列表, 是否来自缓存)"""
    if query in _query_expand_cache:
        return _query_expand_cache[query], True
    result = _query_expand_impl(query)
    _query_expand_cache[query] = result
    return result, False


def _query_expand(query: str) -> list[str]:
    """查询扩展入口（自动降级）"""
    expanded, _ = _query_expand_cached(query)
    return expanded


# ── 分词 ─────────────────────────────────────────────────────────
_jieba_model: Optional[object] = None


def _get_jieba():
    """懒加载 jieba（避免启动开销）"""
    global _jieba_model
    if _jieba_model is None:
        import jieba as _mod
        _jieba_model = _mod
    return _jieba_model


def _jieba_tokenize(text: str) -> list[str]:
    """结巴分词（懒加载）"""
    j = _get_jieba()
    return [w.strip() for w in j.cut(text) if w.strip()]


def _is_low_quality_query(query: str) -> bool:
    """判断查询是否质量过低（太短或无有效字符）"""
    if len(query) < 2:
        return True
    return len(_jieba_tokenize(query)) == 0


# ── 血缘记录 ──────────────────────────────────────────────────────
def _log_lineage(
    source_id: str,
    operation: str,
    reason: str = "",
    target_id: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    """写入记忆血缘记录（用于审计回溯）"""
    import json
    from sinovec_core.db import get_conn
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO memory_lineage (source_id, operation, reason, target_id, details)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                source_id,
                operation,
                reason,
                target_id,
                json.dumps(details) if details else None,
            ))
            conn.commit()
        finally:
            cur.close()


# ── 重排 ─────────────────────────────────────────────────────────
def _rerank(query: str, candidates: list[dict]) -> list[dict]:
    """
    LLM 重排（同步）：
    - 所有候选都分配 rerank_score（LLM 评分或默认 0.5）
    - 候选 <= RERANK_MIN_CANDIDATES：跳过 LLM，直接用混合分数
    - 候选 > RERANK_MIN_CANDIDATES：调用 LLM 完整重排
    """
    for m in candidates:
        m['rerank_score'] = m.get('score', RERANK_DEFAULT_SCORE)

    if len(candidates) <= RERANK_MIN_CANDIDATES:
        logger.info(f"候选 {len(candidates)} <= {RERANK_MIN_CANDIDATES}，跳过 LLM 重排")
        return candidates

    return _rerank_impl(query, candidates)


def temporal_decay_score(
    created_at: Optional[str | datetime],
    half_life_days: int = DECAY_HALF_LIFE_DAYS,
) -> float:
    """
    时间衰减系数：按 half_life_days 指数衰减。
    创建时间越久，系数越接近 0（但永不为零）。
    """
    if created_at is None:
        return 1.0
    # 参数类型校验
    if not isinstance(created_at, (str, datetime)):
        return 1.0
    try:
        if isinstance(created_at, str):
            # 只捕获 datetime 解析异常，不捕获所有 Exception
            try:
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                # 旧格式或无效格式，返回默认系数
                return 1.0
        now_utc = datetime.now(timezone.utc)
        created_utc = (
            created_at
            if created_at.tzinfo is not None
            else created_at.replace(tzinfo=timezone.utc)
        )
        age_days = (now_utc - created_utc).days
        if age_days < 0:
            # 未来时间（时钟偏差），返回最大值
            return 1.0
        return 0.5 ** (age_days / half_life_days)
    except (TypeError, AttributeError, ArithmeticError, OverflowError):
        # 捕获数值计算相关错误，避免静默失败
        return 1.0


def _rerank_impl(query: str, candidates: list[dict]) -> list[dict]:
    """
    LLM 重排实现（同步）。
    R1 修复：对 query 进行基本转义，防止 prompt injection。
    """
    # R1 修复：query 转义（限制长度+去除危险字符）
    safe_query = query[:200].replace("\n", " ").replace("\r", "")
    prompt = (
        f"问题：「{safe_query}」\n"
        "请评估每条记忆与问题的相关性，给出 0~1 之间的分数，"
        "只输出 JSON 数组格式，例如：[0.9, 0.3, 0.8]"
    )
    for r in candidates:
        data = r.get("payload", {}).get("data", "")
        prompt += f"\n- [{r['id'][:8]}] {data[:100]}"

    raw = _ollama_generate(prompt)
    if not raw:
        for r in candidates:
            r.setdefault('rerank_score', r.get('score', RERANK_DEFAULT_SCORE))
        return candidates

    try:
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            import json
            scores = json.loads(raw[start:end])
            for r, s in zip(candidates, scores):
                r['rerank_score'] = max(0.0, min(1.0, float(s)))
    except Exception:
        for r in candidates:
            r.setdefault('rerank_score', r.get('score', RERANK_DEFAULT_SCORE))

    return candidates


# ── 访问热度更新（批量）──────────────────────────────────────────
def _increment_access(mem_ids: list[str]) -> None:
    """命中的记忆批量 UPDATE（一次提交，高效）"""
    if not mem_ids:
        return
    now = datetime.now(timezone.utc).isoformat()
    from sinovec_core.db import get_conn
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE sinovec
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
