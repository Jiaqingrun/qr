from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

from .. import config, db

_SEP = "\x1f"
_REC = "@@@KBREC@@@"


def _find_repos(roots: list[Path], max_depth: int = 4) -> list[Path]:
    repos: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        root = root.resolve()
        for dirpath, dirnames, _ in os.walk(root):
            depth = len(Path(dirpath).relative_to(root).parts)
            if ".git" in dirnames:
                repos.append(Path(dirpath))
                dirnames[:] = []  # 不再深入该仓库
                continue
            if depth >= max_depth:
                dirnames[:] = []
    return repos


def _collect_repo(conn: sqlite3.Connection, repo: Path) -> int:
    key = f"git_last_ts:{repo}"
    last_ts = int(db.get_state(conn, key, "0") or "0")
    fmt = f"{_REC}%H{_SEP}%at{_SEP}%an{_SEP}%s{_SEP}%b"
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", "--no-merges", "--numstat",
             "--date=unix", f"--pretty=format:{fmt}", "-n", "500"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.SubprocessError, OSError):
        return 0
    if out.returncode != 0 or not out.stdout:
        return 0

    project = repo.name
    new = 0
    max_ts = last_ts
    blocks = out.stdout.split(_REC)
    for block in blocks:
        block = block.strip("\n")
        if not block:
            continue
        head, _, rest = block.partition("\n")
        parts = head.split(_SEP)
        if len(parts) < 4:
            continue
        h, ts_s, author, subject = parts[0], parts[1], parts[2], parts[3]
        body = parts[4] if len(parts) > 4 else ""
        try:
            ts = int(ts_s)
        except ValueError:
            continue
        if ts <= last_ts:
            continue
        files = [ln for ln in rest.splitlines() if ln.strip() and "\t" in ln]
        files_txt = "\n".join(files[:50])
        content = f"{subject}\n{body}\n\n变更文件:\n{files_txt}".strip()
        if db.insert_event(conn, uid=f"git:{repo.name}:{h[:12]}", ts=ts, source="git",
                           project=project, title=subject[:120], content=content,
                           meta=f'{{"author": "{author}", "files": {len(files)}}}'):
            new += 1
        max_ts = max(max_ts, ts)
    db.set_state(conn, key, str(max_ts))
    return new


def collect(conn: sqlite3.Connection) -> int:
    cfg = config.load_config()
    roots = config.expand_paths(cfg["git_scan_roots"])
    total = 0
    for repo in _find_repos(roots):
        total += _collect_repo(conn, repo)
    return total
