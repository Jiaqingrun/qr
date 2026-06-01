from __future__ import annotations

import hashlib
import os
import re
import sqlite3

from .. import config, db

_LINE_RE = re.compile(r"^: (\d+):(\d+);(.*)$")


def _iter_history(path: str):
    """解析 zsh 历史，兼容扩展格式(带时间戳)与纯文本格式；支持反斜杠续行。
    返回 (ts_or_None, command)。"""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = _LINE_RE.match(line)
        if m:
            ts = int(m.group(1))
            cmd = m.group(3)
        else:
            ts = None
            cmd = line
        while cmd.endswith("\\") and i + 1 < n:
            i += 1
            cmd = cmd[:-1] + "\n" + lines[i]
        i += 1
        yield ts, cmd


def collect(
    conn: sqlite3.Connection,
    *,
    backfill: bool = False,
    since_ts: int | None = None,
    roots=None,
) -> int:
    cfg = config.load_config()
    path = os.path.expanduser(cfg["shell_history"])
    try:
        file_mtime = int(os.path.getmtime(path))
    except OSError:
        file_mtime = db.now()

    if backfill:
        conn.execute("DELETE FROM events WHERE source='shell'")
        db.set_state(conn, "shell_count", "0")

    last_count = int(db.get_state(conn, "shell_count", "0") or "0")
    entries = list(_iter_history(path))
    new = 0
    start = 0 if backfill else last_count
    for idx in range(start, len(entries)):
        ts, cmd = entries[idx]
        cmd = cmd.strip()
        if not cmd:
            continue
        if backfill:
            if not ts:
                continue
            if since_ts and ts < since_ts:
                continue
        else:
            ts = ts if ts else file_mtime
        h = hashlib.sha1(cmd.encode("utf-8", "replace")).hexdigest()[:10]
        title = cmd.splitlines()[0][:120]
        conn.execute("DELETE FROM events WHERE uid=?", (f"shell:{idx}:{h}",))
        db.insert_event(
            conn,
            uid=f"shell:{idx}:{h}",
            ts=ts,
            source="shell",
            title=title,
            content=cmd,
        )
        new += 1
    db.set_state(conn, "shell_count", str(len(entries)))
    return new
