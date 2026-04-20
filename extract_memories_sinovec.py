#!/usr/bin/env python3
"""
SinoVec - 自动记忆提取脚本
从对话日志中自动提取值得长期记忆的内容
"""

import os
import json
import re
import glob
import hashlib
import uuid
import argparse
import logging
from datetime import datetime, timezone

MAX_LINE_BYTES = 1024 * 1024  # 1MB，超大单行保护阈值
MAX_FILE_BYTES = 256 * 1024 * 1024  # 256MB，单文件总大小保护阈值

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ── 配置(统一从环境变量读取)───────────────────────────────────────
from common import get_conn, get_embedding

LOG_MEMORY_CONTENT = os.getenv("LOG_MEMORY_CONTENT", "false").lower() == "true"

def _detect_sessions_dir() -> str:
    """按优先级尝试找到包含 .jsonl 文件的 session 目录"""
    candidates = [
        os.getenv("SESSIONS_DIR"),
        "/root/.openclaw/agents/main/sessions",
        os.path.expanduser("~/.openclaw/agents/main/sessions"),
    ]
    for d in candidates:
        if d and os.path.isdir(d) and glob.glob(os.path.join(d, "*.jsonl")):
            return d
    return "/root/.openclaw/agents/main/sessions"

SESSIONS_DIR = _detect_sessions_dir()
# 与 memory_sinovec.py 中的 DEDUP_WINDOW_HOURS 保持一致（统一从环境变量读取）
DEDUP_WINDOW_HOURS = int(os.getenv("MEM_DEDUP_WINDOW_HOURS", "6"))

def is_recent(source_id: str) -> bool:
    """
    检查是否在 DEDUP_WINDOW_HOURS 内已提取过(按 source_id 查 created_at)。
    修复:原实现错误使用 last_access_time(该字段从不更新,永远为 NULL),
    导致去重窗口完全失效。现改用 created_at(INSERT 时自动写入)。
    """
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT 1 FROM sinovec
            WHERE payload->>'source_id' = %s
              AND created_at > NOW() - INTERVAL %s
            LIMIT 1
        """, (source_id, f"{DEDUP_WINDOW_HOURS} hours"))
        exists = cur.fetchone() is not None
        cur.close()
    return exists

def save_memory(text: str, source_id: str, user: str = "主人") -> str:
    """保存记忆到数据库。模型降级时 get_embedding 返回全零向量，写入 DB DEFAULT（即全零向量），保持向量列 NOT NULL 约束。"""
    try:
        vec = get_embedding(text)
    except RuntimeError:
        vec = None
    # 全零向量：模型降级，存储为 None（INSERT 使用 DB DEFAULT [0.0]*512）
    if vec is not None and all(v == 0.0 for v in vec):
        vec = None
    pid = str(uuid.uuid4())
    with get_conn() as conn:
        cur = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        payload = json.dumps({"data": text, "user_id": user,
                              "source": "auto_extract", "source_id": source_id,
                              "created_at": now})
        # vec 为 None 时使用零向量（NOT NULL 列且无 DB DEFAULT，必须显式传值）
        vec_to_store = vec if vec is not None else [0.0] * 512
        cur.execute("""
            INSERT INTO sinovec (id, vector, payload, source)
            VALUES (%s, %s::vector, %s::jsonb, 'auto_extract')
        """, (pid, vec_to_store, payload))
        conn.commit()
        cur.close()
    return pid

# ── 记忆提取逻辑 ──────────────────────────────────────────────────────
def extract_from_text(text: str) -> list[str]:
    """从文本中提取值得记忆的内容

    扩展提取策略(覆盖更多有价值的内容):
    - 带编号/符号的列表项(- * 1. 1 等)
    - 标题行(# 标记)
    - 代码块(以 ``` 包裹或行内含冒号的配置)
    - 包含关键决策/结论的长句(以 。!? 结尾且含关键词)
    - 引号内容(「」『』"" 包裹的重要陈述)
    - 含等号/箭头的配置或映射语句
    """
    memories = []
    lines = text.split("\n")
    for line in lines:
        line = line.strip()
        if len(line) < 5:
            continue

        # 提取带编号/符号的列表项
        if re.match(r"^[-*\u2022\u25E6\u2043]\s", line) and len(line) > 10:
            memories.append(line)
        # 提取带数字编号的列表项
        elif re.match(r"^\d+[.)]\s", line) and len(line) > 8:
            memories.append(line)
        # 提取圈号编号(1 2 3)
        elif re.match(r"^[\u2460-\u24ff]\s", line) and len(line) > 5:
            memories.append(line)
        # 提取结论性语句(标题行)
        elif re.match(r"^#{1,3}\s", line) and len(line) > 5:
            memories.append(line)
        # 提取代码块标记行(``` 开始或单独的命令行)
        elif re.match(r"^```", line) or re.match(r"^    ", line):
            if len(line) > 6:
                memories.append(line)
        # 提取配置/映射语句(包含 key=value 或 key => value)
        elif re.search(r"\w+\s*[=<>]+\s*\S+", line) and len(line) > 8:
            memories.append(line)
        # 提取引号内容（「」『』"" 包裹的陈述）
        elif re.search(r'["「『"](.*?)["」』"]', line):
            m = re.search(r'["「『"](.*?)["」』"]', line)
            if m and len(m.group(1)) > 5:
                memories.append(m.group(1))
        # 提取包含关键决策词的长句
        elif re.search(r"(决定|确认|方案|结论|因此|所以|必须|应该|建议|记住|下次|重要)", line):
            if len(line) > 10:
                memories.append(line)
    return memories

def scan_sessions(hours: int = 1) -> list[dict]:
    """扫描最近 N 小时的会话文件"""
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    memories = []
    pattern = os.path.join(SESSIONS_DIR, "*.jsonl")
    for path in glob.glob(pattern):
        if os.path.getmtime(path) < cutoff:
            continue
        # 文件总大小保护：跳过异常大的文件
        try:
            file_size = os.path.getsize(path)
            if file_size > MAX_FILE_BYTES:
                logger.warning(f"会话文件过大（>{MAX_FILE_BYTES//1024//1024}MB），已跳过: {path}")
                continue
        except OSError:
            pass
        try:
            _long_line_warned = False
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if len(line.encode("utf-8")) > MAX_LINE_BYTES:
                        if not _long_line_warned:
                            logger.warning(f"会话文件含超长行（>{MAX_LINE_BYTES//1024}KB），已跳过: {path}")
                            _long_line_warned = True
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
                    except (json.JSONDecodeError, TypeError, KeyError):
                        pass
        except OSError as e:
            logger.warning(f"无法读取会话文件，已跳过: {path} ({e})")
    return memories

def main():
    parser = argparse.ArgumentParser(description="SinoVec 自动记忆提取")
    parser.add_argument("--scan-recent", action="store_true", help="扫描最近会话")
    parser.add_argument("--hours", type=int, default=1, help="扫描最近几小时")
    parser.add_argument("--dry-run", action="store_true", help="仅扫描,不写入数据库")
    args = parser.parse_args()

    if args.scan_recent:
        print(f"扫描最近 {args.hours} 小时的会话...")
        memories = scan_sessions(args.hours)
        saved = 0
        for mem in memories:
            content_hash = hashlib.md5(mem["text"].encode()).hexdigest()[:16]
            if is_recent(content_hash):
                print(f"  ⏭ 跳过: {mem['text'][:50]}...")
                continue
            if args.dry_run:
                if LOG_MEMORY_CONTENT:
                    print(f"  [dry-run] 应写入: {mem['text'][:50]}...")
                else:
                    print(f"  [dry-run] 应写入: [内容已隐藏，设置 LOG_MEMORY_CONTENT=true 显示]")
            else:
                pid = save_memory(mem["text"], content_hash)
                print(f"  ✅ 已写入: {mem['text'][:50]}...")
            saved += 1
        print(f"\n完成: 扫描 {len(memories)} 条,{'本应写入' if args.dry_run else '实际写入'} {saved} 条")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
