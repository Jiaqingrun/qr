from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from pathlib import Path

from .. import config, db, timeutil

_LINE_RE = re.compile(r"^: (\d+):(\d+);(.*)$")


def _iter_history(path: str):
    """解析 zsh 扩展历史（: 开始时间:耗时;命令）；无时间戳的行 yield (None, cmd)。"""
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


def _history_bounds(path: str) -> tuple[int, int]:
    try:
        return timeutil.file_time_bounds(Path(path))
    except OSError:
        return db.now(), db.now()


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
    if not backfill and len(entries) < last_count:
        last_count = 0
    new = 0
    start = 0 if backfill else last_count

    if backfill and entries:
        start_ts, end_ts = _history_bounds(path)
        known = {i: ts for i, (ts, _) in enumerate(entries) if ts is not None}
        est_times = timeutil.interpolate_series(
            len(entries), known, start_ts=start_ts, end_ts=end_ts, step_seconds=30,
        )
    else:
        est_times = None

    for idx in range(start, len(entries)):
        ts, cmd = entries[idx]
        cmd = cmd.strip()
        if not cmd:
            continue
        if backfill:
            if ts is not None:
                use_ts = ts
            elif est_times is not None:
                use_ts = est_times[idx]
            else:
                continue
            if since_ts and use_ts < since_ts:
                continue
        else:
            if ts is not None:
                use_ts = ts
            else:
                continue
        h = hashlib.sha1(cmd.encode("utf-8", "replace")).hexdigest()[:10]
        title = cmd.splitlines()[0][:120]
        conn.execute("DELETE FROM events WHERE uid=?", (f"shell:{idx}:{h}",))
        db.insert_event(
            conn,
            uid=f"shell:{idx}:{h}",
            ts=use_ts,
            source="shell",
            title=title,
            content=cmd,
        )
        new += 1
    db.set_state(conn, "shell_count", str(len(entries)))
    return new
