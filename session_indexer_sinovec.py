#!/usr/bin/env python3
"""
SinoVec - 会话历史索引器
将 AI 对话片段自动索引到向量数据库，支持增量索引（跳过未变化文件）
"""

import os, json, glob, hashlib
from datetime import datetime

SESSIONS_DIR = os.getenv("SESSIONS_DIR", "/root/.openclaw/agents/main/sessions")

# 多路径 fallback，确保非 root 用户也能写入状态文件
def _resolve_state_file() -> str:
    candidates = [
        os.getenv("STATE_FILE"),
        "/var/lib/sinovec/session_indexer_state.json",
    ]
    # 尝试 workspace 路径（用户可写）
    workspace_state = os.path.join(
        os.path.expanduser("~/.openclaw/workspace"), ".sinovec_session_state.json"
    )
    if os.path.isdir(os.path.dirname(workspace_state)) and os.access(os.path.dirname(workspace_state), os.W_OK):
        return workspace_state
    for p in candidates:
        if p:
            dir_ok = os.access(os.path.dirname(p) or "/tmp", os.W_OK)
            if dir_ok:
                return p
    return "/tmp/sinovec_session_indexer_state.json"

STATE_FILE = _resolve_state_file()

# ── 会话索引器配置（STATE_FILE 多路径 fallback）────────────────────
from common import get_conn, get_embedding


def _load_state() -> dict:
    """加载索引状态（文件路径 → {mtime, last_line_count, last_line_hash}）"""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {}


def _save_state(state: dict) -> None:
    """保存索引状态到文件"""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def _file_changed(path: str, state_entry: dict) -> bool:
    """
    判断文件是否发生变化。
    返回 True 表示需要重新扫描，False 表示可跳过。
    同时检测文件是否正在被写入（通过 mtime 最近5分钟内）。
    """
    try:
        stat = os.stat(path)
        mtime = stat.st_mtime
        size = stat.st_size
    except OSError:
        return False  # 文件不存在，跳过

    # 新文件
    if state_entry is None:
        return True

    # mtime 或大小变化
    if abs(mtime - state_entry.get("mtime", 0)) > 1 or size != state_entry.get("size", -1):
        # 文件正在被写入（最近5分钟内修改过）
        import time
        if time.time() - mtime < 300:
            # 还在写入中，本次跳过
            return False
        return True

    return False


def _get_last_line_hash(path: str) -> str:
    """获取文件最后一行的 hash（用于检测文件是否在扫描过程中被修改）"""
    try:
        with open(path, "rb") as f:
            f.seek(-512, os.SEEK_END)
            tail = f.read().decode("utf-8", errors="replace")
        lines = [l for l in tail.strip().split("\n") if l.strip()]
        if lines:
            return hashlib.md5(lines[-1].encode()).hexdigest()[:16]
    except:
        pass
    return ""


# ── 增量索引状态管理 ──────────────────────────────────────────────
STATE_VERSION = 1  # 状态格式版本，用于未来兼容


def is_duplicate(source_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM sinovec WHERE payload->>'source_id' = %s LIMIT 1", (source_id,))
        exists = cur.fetchone() is not None
        cur.close()
    return exists


def save_fragment(text: str, session_id: str, source_id: str) -> str:
    import uuid
    vec = get_embedding(text)
    pid = str(uuid.uuid4())
    with get_conn() as conn:
        cur = conn.cursor()
        payload = json.dumps({
            "data": text[:500],
            "user_id": "会话",
            "source": "session",
            "session_id": session_id,
            "source_id": source_id
        })
        cur.execute("""
            INSERT INTO sinovec (id, vector, payload)
            VALUES (%s, %s::vector, %s::jsonb)
        """, (pid, vec, payload))
        conn.commit()
        cur.close()
    return pid


def index_sessions(dry_run: bool = False):
    state = _load_state()
    files = glob.glob(os.path.join(SESSIONS_DIR, "*.jsonl"))

    if not files:
        print("未找到 session 文件")
        return 0

    print(f"找到 {len(files)} 个 session 文件")
    state_version = state.get("_v", 0)

    # 如果状态版本不对，清空旧状态
    if state_version != STATE_VERSION:
        print(f"状态版本变更（{state_version} → {STATE_VERSION}），重建索引状态")
        state = {"_v": STATE_VERSION}

    new_state = {"_v": STATE_VERSION}
    saved = 0
    skipped = 0
    changed = 0

    for path in sorted(files, key=os.path.getmtime, reverse=True):
        session_id = os.path.basename(path).replace(".jsonl", "")
        entry = state.get(path)

        if not _file_changed(path, entry):
            skipped += 1
            # 文件未变，保留状态（复制到 new_state）
            new_state[path] = entry
            continue

        # 文件有变化（含新文件），记录当前 mtime/size 快照
        try:
            stat = os.stat(path)
            current_mtime = stat.st_mtime
            current_size = stat.st_size
        except OSError:
            continue

        changed += 1
        last_line_hash_before = _get_last_line_hash(path)

        try:
            with open(path, encoding="utf-8") as f:
                messages = [json.loads(l) for l in f if l.strip()]

            line_count = len(messages)
            prev_line_count = entry.get("line_count", 0) if entry else 0

            # 仅处理新增的消息行（增量索引）
            start_i = prev_line_count
            if start_i >= line_count:
                # 行数没变但 mtime 变了（可能文件被重写），从头处理
                start_i = 0

            new_entries_this_file = 0
            for i in range(start_i, line_count):
                msg = messages[i]
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
                source_id = f"{session_id}__{i}"  # 双下划线分隔，避免 session_id 含下划线时解析歧义
                if is_duplicate(source_id):
                    continue
                if dry_run:
                    print(f"  [dry-run] 应写入: {content[:50]}...")
                else:
                    save_fragment(content, session_id, source_id)
                new_entries_this_file += 1
                saved += 1
                if saved % 50 == 0:
                    print(f"  已处理 {saved} 个片段...")

            # 扫描过程中检测文件是否被追加（最后一行的 hash 变了）
            last_line_hash_after = _get_last_line_hash(path)
            if last_line_hash_after != last_line_hash_before and last_line_hash_after != "":
                # 扫描过程中文件被追加了，下次再扫
                print(f"  ⚠️  文件在扫描中被追加，跳过: {os.path.basename(path)}")
                new_state[path] = {
                    "mtime": current_mtime,
                    "size": current_size,
                    "line_count": line_count,
                    "last_line_hash": last_line_hash_after
                }
                continue

            # 正常结束，记录最终状态
            new_state[path] = {
                "mtime": current_mtime,
                "size": current_size,
                "line_count": line_count,
                "last_line_hash": last_line_hash_after
            }

            if new_entries_this_file > 0:
                print(f"  ✅ {os.path.basename(path)}: 新增 {new_entries_this_file} 条")

        except Exception as e:
            print(f"  ⚠️  处理失败 {os.path.basename(path)}: {e}")
            # 保留当前快照状态（不更新 line_count，避免重复处理）
            new_state[path] = {
                "mtime": current_mtime,
                "size": current_size,
                "line_count": entry.get("line_count", 0) if entry else 0,
                "last_line_hash": ""
            }

    _save_state(new_state)

    action = "扫描" if dry_run else "索引"
    print(f"\n✅ {action}完成: 新增 {saved} 条，变化 {changed} 个文件，跳过 {skipped} 个未变文件")
    return saved


def main():
    import argparse
    parser = argparse.ArgumentParser(description="SinoVec 会话索引")
    sub = parser.add_subparsers(dest="cmd")
    index_parser = sub.add_parser("index", help="索引会话历史（增量）")
    index_parser.add_argument("--dry-run", action="store_true", help="仅扫描，不写入数据库")
    sub.add_parser("check", help="检查索引状态")
    sub.add_parser("reset", help="重置索引状态（强制全量重建）")
    args = parser.parse_args()

    if args.cmd == "index":
        index_sessions(dry_run=getattr(args, "dry_run", False))
    elif args.cmd == "check":
        state = _load_state()
        print(f"已追踪 {len([k for k in state if not k.startswith('_')])} 个 session 文件")
        print(f"状态版本: {state.get('_v', 0)}")
    elif args.cmd == "reset":
        try:
            os.remove(STATE_FILE)
            print(f"✅ 已删除状态文件 {STATE_FILE}，下次 index 将全量重建")
        except FileNotFoundError:
            print("状态文件不存在，无需重置")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
