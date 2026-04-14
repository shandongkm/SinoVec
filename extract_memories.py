#!/usr/bin/env python3
"""
SinoVec - 自动记忆提取脚本
从对话日志中自动提取值得长期记忆的内容
"""

import os, sys, json, re, glob
from datetime import datetime

# ── 配置 ──────────────────────────────────────────────────────────────
SESSIONS_DIR = os.getenv("SESSIONS_DIR", "/root/.openclaw/agents/main/sessions")
MEMORY_DB = {
    "host": os.getenv("MEMORY_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("MEMORY_DB_PORT", "5432")),
    "database": os.getenv("MEMORY_DB_NAME", "memory"),
    "user": os.getenv("MEMORY_DB_USER", "postgres"),
    "password": os.getenv("MEMORY_DB_PASS", ""),
}
DEDUP_WINDOW_HOURS = 6  # 6小时内重复内容跳过

# ── 向量生成 ──────────────────────────────────────────────────────────
def get_embedding(text: str) -> list:
    """使用 FastEmbed 生成向量"""
    import urllib.request, json as json_mod
    hf_proxy = os.getenv("HF_HUB_PROXY", "")
    if hf_proxy:
        os.environ["HF_HUB_PROXY"] = hf_proxy
    from fastembed import TextEmbedding
    model = TextEmbedding("BAAI/bge-small-zh-v1.5")
    arr = list(model.embed([text]))[0]
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
    cur.execute("""
        INSERT INTO mem0 (id, vector, payload, fts)
        VALUES (%s, %s::vector, %s::jsonb, to_tsvector('simple', %s))
    """, (pid, vec, payload, text))
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
                        inner = msg.get("message", msg)  # 兼容嵌套格式
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
            pid = save_memory(mem["text"], content_hash)
            print(f"  ✅ 已写入: {mem['text'][:50]}...")
            saved += 1
        print(f"\n完成: 提取 {len(memories)} 条，跳过重复 {len(memories)-saved} 条")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
