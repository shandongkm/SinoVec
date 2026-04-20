#!/usr/bin/env python3
"""
SinoVec - 会话历史索引器
将 AI 对话片段自动索引到向量数据库，支持增量索引（跳过未变化文件）
"""

import os
import json
import glob
import hashlib
import fcntl
import re
import uuid
import argparse
import time
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

SESSIONS_DIR = os.getenv("SESSIONS_DIR", "/root/.openclaw/agents/main/sessions")
STABLE_SECONDS = int(os.getenv("SESSION_INDEX_STABLE_SECONDS", "300"))

# ── 状态文件路径解析（多路径 fallback，确保非 root 用户也能写入）─────
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
    # 回退到固定路径（避免 UUID 每次运行变化导致增量索引失效）
    return "/tmp/sinovec_session_indexer.json"

STATE_FILE = _resolve_state_file()

# ── 会话索引器配置（STATE_FILE 多路径 fallback）────────────────────
from common import get_conn, get_embedding

LOG_MEMORY_CONTENT = os.getenv("LOG_MEMORY_CONTENT", "false").lower() == "true"


def _load_state() -> dict:
    """加载索引状态（文件路径 → {mtime, last_line_count, last_line_hash}）"""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        # 文件不存在/损坏/无权限 → 从空状态开始（强制全量重建）
        return {}


def _save_state(state: dict) -> None:
    """保存索引状态到文件"""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def _file_changed(path: str, state_entry: dict, force: bool = False) -> bool:
    """
    判断文件是否发生变化。
    返回 True 表示需要重新扫描，False 表示可跳过。
    force=True 时跳过稳定期检测，强制索引。
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
        if not force and time.time() - mtime < STABLE_SECONDS:
            # 文件还在稳定期内，本次跳过
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
    except (OSError, IOError, IndexError, UnicodeDecodeError):
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
    """存储会话片段。模型降级时 get_embedding 返回全零向量，写入 DB DEFAULT（即全零向量），保持向量列 NOT NULL 约束。"""
    try:
        vec = get_embedding(text)
    except RuntimeError:
        vec = None
    # 全零向量：模型降级，存储为 None（INSERT 使用 DB DEFAULT [0.0]*512）
    if vec is not None and all(v == 0.0 for v in vec):
        vec = None
    pid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.cursor()
        payload = json.dumps({
            "data": text[:500],
            "user_id": "会话",
            "source": "session",
            "session_id": session_id,
            "source_id": source_id,
            "created_at": now
        })
        # vec 为 None 时使用零向量（NOT NULL 列且无 DB DEFAULT，必须显式传值）
        vec_to_store = vec if vec is not None else [0.0] * 512
        cur.execute("""
            INSERT INTO sinovec (id, vector, payload, source)
            VALUES (%s, %s::vector, %s::jsonb, 'session')
        """, (pid, vec_to_store, payload))
        conn.commit()
        cur.close()
    return pid


def _acquire_index_lock():
    """获取索引锁，返回 lock_file 对象；失败返回 None"""
    lock_path = os.getenv("INDEX_LOCK_FILE", "/var/lib/sinovec/session_indexer.lock")
    lock_dir = os.path.dirname(lock_path) or "."
    if not os.path.isdir(lock_dir):
        try:
            os.makedirs(lock_dir, exist_ok=True)
        except OSError:
            lock_path = "/tmp/sinovec_session_indexer.lock"
    try:
        lock_file = open(lock_path, "w")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file
    except (IOError, OSError):
        return None

def _release_index_lock(lock_file):
    """释放索引锁"""
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
    except Exception:
        pass

class _IndexLock:
    """上下文管理器：自动获取/释放索引锁，防止并发运行"""
    def __enter__(self):
        self._lock_file = _acquire_index_lock()
        if self._lock_file is None:
            raise RuntimeError("另一个索引任务正在运行")
        return self._lock_file
    def __exit__(self, *args):
        if self._lock_file is not None:
            _release_index_lock(self._lock_file)

def index_sessions(dry_run: bool = False, force: bool = False):
    try:
        with _IndexLock() as lock_file:
            return _index_sessions_inner(dry_run, force)
    except RuntimeError as e:
        print(e)
        return None

def _index_sessions_inner(dry_run: bool = False, force: bool = False):
    state = _load_state()
    files = glob.glob(os.path.join(SESSIONS_DIR, "*.jsonl"))

    if not files:
        print("未找到 session 文件")
        return 0

    print(f"找到 {len(files)} 个 session 文件")
    state_version = state.get("_v", 0)

    if state_version != STATE_VERSION:
        print(f"状态版本变更（{state_version} → {STATE_VERSION}），重建索引状态")
        state = {"_v": STATE_VERSION}

    new_state = {"_v": STATE_VERSION}
    saved = 0
    skipped = 0
    changed = 0

    for path in sorted(files, key=os.path.getmtime, reverse=True):
        session_id = os.path.basename(path).replace(".jsonl", "")
        # session_id 中的特殊字符转义，防止影响 SQL 或文件名解析
        _sg_chars = "'\"\\"
        safe_session_id = re.sub("[%s]" % _sg_chars, '_', session_id)
        del _sg_chars
        entry = state.get(path)

        if not _file_changed(path, entry, force=force):
            skipped += 1
            new_state[path] = entry
            continue

        try:
            stat = os.stat(path)
            current_mtime = stat.st_mtime
            current_size = stat.st_size
        except OSError:
            skipped += 1
            continue

        changed += 1
        last_line_hash_before = _get_last_line_hash(path)

        try:
            with open(path, encoding="utf-8") as f:
                messages = [json.loads(l) for l in f if l.strip()]

            line_count = len(messages)
            prev_line_count = entry.get("line_count", 0) if entry else 0

            start_i = prev_line_count
            if start_i >= line_count:
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
                source_id = f"{safe_session_id}__{i}"
                if is_duplicate(source_id):
                    continue
                if dry_run:
                    if LOG_MEMORY_CONTENT:
                        print(f"  [dry-run] 应写入: {content[:50]}...")
                    else:
                        print(f"  [dry-run] 应写入: [内容已隐藏，设置 LOG_MEMORY_CONTENT=true 显示]")
                else:
                    save_fragment(content, session_id, source_id)
                new_entries_this_file += 1
                saved += 1
                if saved % 50 == 0:
                    print(f"  已处理 {saved} 个片段...")

            last_line_hash_after = _get_last_line_hash(path)
            if last_line_hash_after != last_line_hash_before and last_line_hash_after != "":
                print(f"  ⚠️  文件在扫描中被追加，跳过: {os.path.basename(path)}")
                new_state[path] = {
                    "mtime": current_mtime,
                    "size": current_size,
                    "line_count": line_count,
                    "last_line_hash": last_line_hash_after
                }
                continue

            new_state[path] = {
                "mtime": current_mtime,
                "size": current_size,
                "line_count": line_count,
                "last_line_hash": last_line_hash_after
            }

            if new_entries_this_file > 0:
                print(f"  ✅ {os.path.basename(path)}: 新增 {new_entries_this_file} 条")

        except (json.JSONDecodeError, TypeError, KeyError, OSError) as e:
            print(f"  ⚠️  处理失败 {os.path.basename(path)}: {e}")
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
    parser = argparse.ArgumentParser(description="SinoVec 会话索引")
    sub = parser.add_subparsers(dest="cmd")
    index_parser = sub.add_parser("index", help="索引会话历史（增量）")
    index_parser.add_argument("--dry-run", action="store_true", help="仅扫描，不写入数据库")
    index_parser.add_argument("--force", action="store_true", help="强制索引，忽略文件稳定期检测")
    sub.add_parser("check", help="检查索引状态")
    sub.add_parser("reset", help="重置索引状态（强制全量重建）")
    args = parser.parse_args()

    if args.cmd == "index":
        index_sessions(dry_run=getattr(args, "dry_run", False),
                      force=getattr(args, "force", False))
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
