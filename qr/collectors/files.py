from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .. import config, db, scan_paths


def _project_of(path: Path, root: Path) -> str:
    from .. import workspace

    return workspace.project_from_path(path, root)

_BACKFILL_CAP = 20_000


def collect(
    conn: sqlite3.Connection,
    *,
    backfill: bool = False,
    since_ts: int | None = None,
    roots=None,
) -> int:
    cfg = config.load_config()
    scan = roots if roots else config.expand_paths(cfg["index_roots"])
    exclude = set(cfg["index_exclude_dirs"])
    exts = set(cfg["index_extensions"])
    cap = _BACKFILL_CAP if backfill else int(cfg["files_collect_cap"])

    if backfill:
        conn.execute("DELETE FROM events WHERE source='file'")
        db.set_state(conn, "files_last_mtime", "0")

    last_ts = float(db.get_state(conn, "files_last_mtime", "0") or "0")
    if backfill and since_ts:
        last_ts = float(since_ts) - 1
    max_mtime = last_ts
    new = 0
    seen = 0
    for root in scan:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            scan_paths.prune_walk_dirnames(dirnames, Path(dirpath))
            dirnames[:] = [
                d for d in dirnames
                if d not in exclude and not d.startswith(".") and not d.endswith(".egg-info")
            ]
            for fn in filenames:
                if Path(fn).suffix.lower() not in exts:
                    continue
                p = Path(dirpath) / fn
                try:
                    mt = p.stat().st_mtime
                except OSError:
                    continue
                if mt <= last_ts:
                    continue
                if since_ts and mt < since_ts:
                    continue
                if seen >= cap:
                    break
                seen += 1
                project = _project_of(p, root)
                rel = str(p)
                uid = f"file:{rel}:{int(mt)}"
                conn.execute("DELETE FROM events WHERE uid=?", (uid,))
                db.insert_event(
                    conn,
                    uid=uid,
                    ts=int(mt),
                    source="file",
                    project=project,
                    title=fn,
                    content=rel,
                )
                new += 1
                max_mtime = max(max_mtime, mt)
            if seen >= cap:
                break
        if seen >= cap:
            break
    db.set_state(conn, "files_last_mtime", repr(max_mtime))
    return new
