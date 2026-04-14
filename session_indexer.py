#!/usr/bin/env python3
"""
SinoVec - 会话历史索引器
将 AI 对话片段自动索引到向量数据库
"""

import os, json, glob, hashlib
from datetime import datetime

SESSIONS_DIR = os.getenv("SESSIONS_DIR", "/root/.openclaw/agents/main/sessions")

MEMORY_DB = {
    "host": os.getenv("MEMORY_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("MEMORY_DB_PORT", "5432")),
    "database": os.getenv("MEMORY_DB_NAME", "memory"),
    "user": os.getenv("MEMORY_DB_USER", "postgres"),
    "password": os.getenv("MEMORY_DB_PASS", ""),
}

def get_embedding(text: str) -> list:
    hf_proxy = os.getenv("HF_HUB_PROXY", "")
    if hf_proxy:
        os.environ["HF_HUB_PROXY"] = hf_proxy
    from fastembed import TextEmbedding
    model = TextEmbedding("BAAI/bge-small-zh-v1.5")
    arr = list(model.embed([text]))[0]
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
        "data": text[:500],  # 限制长度
        "user_id": "会话",
        "source": "session",
        "session_id": session_id,
        "source_id": source_id
    })
    cur.execute("""
        INSERT INTO mem0 (id, vector, payload, fts)
        VALUES (%s, %s::vector, %s::jsonb, to_tsvector('simple', %s))
    """, (pid, vec, payload, text))
    conn.commit()
    cur.close()
    conn.close()
    return pid

def index_sessions():
    files = glob.glob(os.path.join(SESSIONS_DIR, "*.jsonl"))
    print(f"找到 {len(files)} 个 session 文件")
    saved = 0
    for path in sorted(files, key=os.path.getmtime, reverse=True)[:10]:  # 最近10个
        session_id = os.path.basename(path).replace(".jsonl", "")
        try:
            with open(path, encoding="utf-8") as f:
                messages = [json.loads(l) for l in f if l.strip()]
            for i, msg in enumerate(messages):
                if msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    if len(content) < 20:
                        continue
                    source_id = f"{session_id}_{i}"
                    if is_duplicate(source_id):
                        continue
                    pid = save_fragment(content, session_id, source_id)
                    saved += 1
                    if saved % 50 == 0:
                        print(f"  已处理 {saved} 个片段...")
        except Exception as e:
            print(f"  ⚠️  处理失败 {path}: {e}")
    print(f"✅ 索引完成: 新增 {saved} 个片段")
    return saved

def main():
    import argparse
    parser = argparse.ArgumentParser(description="SinoVec 会话索引")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("index", help="索引会话历史")
    sub.add_parser("check", help="检查索引状态")
    args = parser.parse_args()
    if args.cmd == "index":
        index_sessions()
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
