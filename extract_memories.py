#!/usr/bin/env python3
"""
SinoVec - 自动记忆提取脚本
从对话日志中自动提取值得长期记忆的内容
"""

import os, sys, json, re, glob
from datetime import datetime

# ── 配置（统一从环境变量读取）───────────────────────────────────────
SESSIONS_DIR = os.getenv("SESSIONS_DIR", "/root/.openclaw/agents/main/sessions")
_db_pass = os.getenv("MEMORY_DB_PASS", "")
if not _db_pass:
    raise RuntimeError(
        "MEMORY_DB_PASS environment variable is not set. "
        "Please set it before running. "
        "Example: export MEMORY_DB_PASS=your_secure_password"
    )
MEMORY_DB = {
    "host": os.getenv("MEMORY_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("MEMORY_DB_PORT", "5433")),
    "database": os.getenv("MEMORY_DB_NAME", "memory"),
    "user": os.getenv("MEMORY_DB_USER", "openclaw"),
    "password": _db_pass,
}
DEDUP_WINDOW_HOURS = 6  # 6小时内重复内容跳过

# ── 向量生成（全局单例，避免重复加载模型）─────────────────────────────
_embedding_model = None
_embedding_lock = __import__("threading").Lock()

def get_embedding(text: str) -> list:
    """使用 FastEmbed 生成向量（全局模型单例）"""
    global _embedding_model
    if _embedding_model is None:
        with _embedding_lock:
            if _embedding_model is None:
                hf_proxy = os.getenv("HF_HUB_PROXY", "")
                if hf_proxy:
                    os.environ["HF_HUB_PROXY"] = hf_proxy
                from fastembed import TextEmbedding
                _embedding_model = TextEmbedding("BAAI/bge-small-zh-v1.5")
    arr = list(_embedding_model.embed([text]))[0]
    return [float(x) for x in arr]

# ── 数据库 ────────────────────────────────────────────────────────────
import psycopg2

def db_conn():
    return psycopg2.connect(**MEMORY_DB)

def is_recent(source_id: str) -> bool:
    """检查是否在 DEDUP_WINDOW_HOURS 内已提取过（按 source_id 查 payload）"""
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT 1 FROM mem0
        WHERE payload->>'source_id' = %s
          AND last_access_time > NOW() - INTERVAL '%s hours'
        LIMIT 1
    """, (source_id, DEDUP_WINDOW_HOURS))
    exists = cur.fetchone() is not None
    cur.close()
    conn.close()
    return exists

def save_memory(text: str, source_id: str, user: str = "主人") -> str:
    """保存记忆到数据库"""
    import uuid
    vec = get_embedding(text)
    pid = str(uuid.uuid4())
    conn = db_conn()
    cur = conn.cursor()
    payload = json.dumps({"data": text, "user_id": user, "source": "auto_extract", "source_id": source_id})
    # 修复：移除 fts 手动插入，由数据库生成列自动计算
    cur.execute("""
        INSERT INTO mem0 (id, vector, payload)
        VALUES (%s, %s::vector, %s::jsonb)
    """, (pid, vec, payload))
    conn.commit()
    cur.close()
    conn.close()
    return pid

# ── 记忆提取逻辑 ──────────────────────────────────────────────────────
def extract_from_text(text: str) -> list[str]:
    """从文本中提取值得记忆的内容"""
    memories = []
    lines = text.split("\n")
    for line in lines:
        line = line.strip()
        # 提取带编号的列表项
        if re.match(r"^[-*]\s", line) and len(line) > 10:
            memories.append(line)
        # 提取结论性语句
        elif re.match(r"^#{1,3}\s", line) and len(line) > 5:
            memories.append(line)
        # 提取代码块（关键配置）
        elif line.startswith("`") and ":" in line:
            memories.append(line.strip("`"))
    return memories

def scan_sessions(hours: int = 1) -> list[dict]:
    """扫描最近 N 小时的会话文件"""
    cutoff = datetime.now().timestamp() - hours * 3600
    memories = []
    pattern = os.path.join(SESSIONS_DIR, "*.jsonl")
    for path in glob.glob(pattern):
        if os.path.getmtime(path) < cutoff:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                        inner = msg.get("message", msg)
                        role = inner.get("role", "")
                        if role != "user":
                            continue
                        raw = inner.get("content", "")
                        if isinstance(raw, str):
                            text_content = raw
                        elif isinstance(raw, list):
                            parts = []
                            for block in raw:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    parts.append(block.get("text", ""))
                            text_content = " ".join(parts)
                        else:
                            text_content = ""
                        for mem in extract_from_text(text_content):
                            if len(mem) > 10:
                                memories.append({"text": mem, "source": os.path.basename(path)})
                    except:
                        pass
        except:
            pass
    return memories

def main():
    import argparse
    parser = argparse.ArgumentParser(description="SinoVec 自动记忆提取")
    parser.add_argument("--scan-recent", action="store_true", help="扫描最近会话")
    parser.add_argument("--hours", type=int, default=1, help="扫描最近几小时")
    parser.add_argument("--dry-run", action="store_true", help="仅扫描，不写入数据库")
    args = parser.parse_args()

    if args.scan_recent:
        print(f"扫描最近 {args.hours} 小时的会话...")
        memories = scan_sessions(args.hours)
        saved = 0
        for mem in memories:
            import hashlib
            content_hash = hashlib.md5(mem["text"].encode()).hexdigest()[:16]
            if is_recent(content_hash):
                print(f"  ⏭ 跳过: {mem['text'][:50]}...")
                continue
            if args.dry_run:
                print(f"  [dry-run] 应写入: {mem['text'][:50]}...")
            else:
                pid = save_memory(mem["text"], content_hash)
                print(f"  ✅ 已写入: {mem['text'][:50]}...")
            saved += 1
        print(f"\n完成: 扫描 {len(memories)} 条，{'本应写入' if args.dry_run else '实际写入'} {saved} 条")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
