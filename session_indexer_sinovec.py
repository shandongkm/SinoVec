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

SESSIONS_DIR = os.getenv("SESSIONS_DIR", os.path.expanduser("~/.openclaw/agents/main/sessions"))
STABLE_SECONDS = int(os.getenv("SESSION_INDEX_STABLE_SECONDS", "300"))

# ── session_indexer 与包的 DB 连接共用 common.py ──────────────────
# 注意：session_indexer 直接导入 common.py（与包内 db.py 共用 DB_CONFIG）
from common import get_conn, get_embedding

LOG_MEMORY_CONTENT = os.getenv("LOG_MEMORY_CONTENT", "false").lower() == "true"


# ── 状态文件路径解析（多路径 fallback，按优先级尝试）─────────────
def _resolve_state_file() -> str:
    # 优先级1：环境变量指定
    if os.getenv("STATE_FILE"):
        return os.getenv("STATE_FILE")
    # 优先级2：sinovec 服务用户专属目录（install.sh 已 chown sinovec:sinovec）
    sinovec_home = os.environ.get("SINOVEC_HOME", os.getenv("SINOVEC_HOME", ""))
    if sinovec_home:
        sinovec_var = os.path.join(sinovec_home, "var")
        os.makedirs(sinovec_var, exist_ok=True)
        if os.access(sinovec_var, os.W_OK):
            return os.path.join(sinovec_var, "session_indexer_state.json")
    # 优先级3：var 目录（系统管理员可提前创建并授权）
    var_path = "/var/lib/sinovec/session_indexer_state.json"
    if os.access("/var/lib/sinovec", os.W_OK):
        return var_path
    # 优先级4：workspace 回退（仅普通用户可写，不适合 sinovec 服务用户）
    workspace_state = os.path.join(
        os.path.expanduser("~/.openclaw/workspace"), ".sinovec_session_state.json"
    )
    if os.path.isdir(os.path.dirname(workspace_state)) and os.access(os.path.dirname(workspace_state), os.W_OK):
        return workspace_state
    # 优先级5：tmp 回退（避免因路径无写权限导致增量索引完全失效）
    return "/tmp/sinovec_session_indexer.json"


STATE_FILE = _resolve_state_file()


def _load_state() -> dict:
    """加载索引状态（文件路径 → {mtime, last_line_count, last_line_hash}）"""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        # 文件不存在/损坏/无权限 → 从空状态开始（强制全量重建）
        return {}


def _save_state(state: dict) -> None:
    """保存索引状态到文件（权限 600，仅服务用户可读写）"""
    dir_path = os.path.dirname(STATE_FILE)
    os.makedirs(dir_path, exist_ok=True)
    # 设置目录权限为 700（仅服务用户可访问）
    try:
        os.chmod(dir_path, 0o700)
    except OSError:
        pass
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    # 设置文件权限为 600（防止敏感信息泄露）
    os.chmod(tmp, 0o600)
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
        cur.execute(
            "SELECT 1 FROM sinovec WHERE payload->>'source_id' = %s LIMIT 1",
            (source_id,),
        )
        exists = cur.fetchone() is not None
        cur.close()
    return exists


def save_fragment(text: str, session_id: str, source_id: str) -> str:
    """
    存储会话片段。
    模型降级时 get_embedding 返回全零向量，写入 DB DEFAULT（即全零向量），
    保持向量列 NOT NULL 约束。
    """
    try:
        vec = get_embedding(text)
    except RuntimeError:
        vec = None

    # 全零向量：模型降级，存储为 None（INSERT 使用零向量）
    if vec is not None and all(v == 0.0 for v in vec):
        vec = None

    pid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps({
        "data": text[:2000],
        "user_id": "会话",
        "source": "session",
        "session_id": session_id,
        "source_id": source_id,
        "created_at": now,
    })
    # vec 为 None 时使用零向量（NOT NULL 列且无 DB DEFAULT，必须显式传值）
    vec_to_store = vec if vec is not None else [0.0] * 512

    with get_conn() as conn:
        cur = conn.cursor()
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
        safe_session_id = re.sub(f"[{re.escape(_sg_chars)}]", "_", session_id)
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
            start_i = prev_line_count if prev_line_count < line_count else 0

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
                    save_fragment(content, safe_session_id, source_id)
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
                    "last_line_hash": last_line_hash_after,
                }
                continue

            new_state[path] = {
                "mtime": current_mtime,
                "size": current_size,
                "line_count": line_count,
                "last_line_hash": last_line_hash_after,
            }

            if new_entries_this_file > 0:
                print(f"  ✅ {os.path.basename(path)}: 新增 {new_entries_this_file} 条")

        except (json.JSONDecodeError, TypeError, KeyError, OSError) as e:
            print(f"  ⚠️  处理失败 {os.path.basename(path)}: {e}")
            new_state[path] = {
                "mtime": current_mtime,
                "size": current_size,
                "line_count": entry.get("line_count", 0) if entry else 0,
                "last_line_hash": "",
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
        index_sessions(
            dry_run=getattr(args, "dry_run", False),
            force=getattr(args, "force", False),
        )
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
