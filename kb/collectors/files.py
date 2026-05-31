from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .. import config, db


def _project_of(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root)
        return rel.parts[0] if rel.parts else root.name
    except ValueError:
        return root.name


def collect(conn: sqlite3.Connection) -> int:
    cfg = config.load_config()
    roots = config.expand_paths(cfg["index_roots"])
    exclude = set(cfg["index_exclude_dirs"])
    exts = set(cfg["index_extensions"])
    cap = int(cfg["files_collect_cap"])
    last_ts = float(db.get_state(conn, "files_last_mtime", "0") or "0")
    max_mtime = last_ts
    new = 0
    seen = 0
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in exclude and not d.startswith(".")]
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
                if seen >= cap:
                    break
                seen += 1
                project = _project_of(p, root)
                rel = str(p)
                uid = f"file:{rel}:{int(mt)}"
                if db.insert_event(conn, uid=uid, ts=int(mt), source="file",
                                   project=project, title=fn, content=rel):
                    new += 1
                max_mtime = max(max_mtime, mt)
    db.set_state(conn, "files_last_mtime", repr(max_mtime))
    return new
