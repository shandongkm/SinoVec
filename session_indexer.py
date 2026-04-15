#!/usr/bin/env python3
"""
SinoVec - 会话历史索引器
将 AI 对话片段自动索引到向量数据库
"""

import os, json, glob, hashlib
from datetime import datetime

SESSIONS_DIR = os.getenv("SESSIONS_DIR", "/root/.openclaw/agents/main/sessions")

# ── 配置（统一从环境变量读取，与 memory_layer.py 一致）───────────────
MEMORY_DB = {
    "host": os.getenv("MEMORY_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("MEMORY_DB_PORT", "5433")),  # 修正
    "database": os.getenv("MEMORY_DB_NAME", "memory"),
    "user": os.getenv("MEMORY_DB_USER", "openclaw"),   # 修正
    "password": os.getenv("MEMORY_DB_PASS", "naZytYn2hKsy"),
}

# ── 向量生成（全局单例）─────────────────────────────────────────────
_embedding_model = None
_embedding_lock = __import__("threading").Lock()

def get_embedding(text: str) -> list:
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

def is_duplicate(source_id: str) -> bool:
    import psycopg2
    conn = psycopg2.connect(**MEMORY_DB)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM mem0 WHERE payload->>'source_id' = %s LIMIT 1", (source_id,))
    exists = cur.fetchone() is not None
    cur.close()
    conn.close()
    return exists

def save_fragment(text: str, session_id: str, source_id: str) -> str:
    import psycopg2, uuid
    vec = get_embedding(text)
    pid = str(uuid.uuid4())
    conn = psycopg2.connect(**MEMORY_DB)
    cur = conn.cursor()
    payload = json.dumps({
        "data": text[:500],
        "user_id": "会话",
        "source": "session",
        "session_id": session_id,
        "source_id": source_id
    })
    # 修复：移除 fts 手动插入，由数据库生成列自动计算
    cur.execute("""
        INSERT INTO mem0 (id, vector, payload)
        VALUES (%s, %s::vector, %s::jsonb)
    """, (pid, vec, payload))
    conn.commit()
    cur.close()
    conn.close()
    return pid

def index_sessions(dry_run: bool = False):
    files = glob.glob(os.path.join(SESSIONS_DIR, "*.jsonl"))
    print(f"找到 {len(files)} 个 session 文件")
    saved = 0
    for path in sorted(files, key=os.path.getmtime, reverse=True)[:10]:
        session_id = os.path.basename(path).replace(".jsonl", "")
        try:
            with open(path, encoding="utf-8") as f:
                messages = [json.loads(l) for l in f if l.strip()]
            for i, msg in enumerate(messages):
                inner = msg.get("message", msg)
                role = inner.get("role", "")
                if role != "assistant":
                    continue
                raw_content = inner.get("content", "")
                if isinstance(raw_content, str):
                    content = raw_content
                elif isinstance(raw_content, list):
                    parts = []
                    for block in raw_content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                parts.append(block.get("text", ""))
                            elif block.get("type") == "output":
                                parts.append(block.get("text", ""))
                    content = " ".join(parts)
                else:
                    content = ""
                if len(content) < 20:
                    continue
                source_id = f"{session_id}_{i}"
                if is_duplicate(source_id):
                    continue
                if dry_run:
                    print(f"  [dry-run] 应写入: {content[:50]}...")
                else:
                    pid = save_fragment(content, session_id, source_id)
                saved += 1
                if saved % 50 == 0:
                    print(f"  已处理 {saved} 个片段...")
        except Exception as e:
            print(f"  ⚠️  处理失败 {path}: {e}")
    action = "扫描" if dry_run else "索引"
    print(f"✅ {action}完成: 新增 {saved} 个片段")
    return saved

def main():
    import argparse
    parser = argparse.ArgumentParser(description="SinoVec 会话索引")
    sub = parser.add_subparsers(dest="cmd")
    index_parser = sub.add_parser("index", help="索引会话历史")
    index_parser.add_argument("--dry-run", action="store_true", help="仅扫描，不写入数据库")
    sub.add_parser("check", help="检查索引状态")
    args = parser.parse_args()
    if args.cmd == "index":
        index_sessions(dry_run=getattr(args, 'dry_run', False))
    elif args.cmd == "check":
        import psycopg2
        conn = psycopg2.connect(**MEMORY_DB)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM mem0 WHERE source = 'session'")
        print(f"已索引 session 片段: {cur.fetchone()[0]}")
        cur.close()
        conn.close()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
