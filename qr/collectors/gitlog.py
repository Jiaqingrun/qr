from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
from pathlib import Path

from .. import config, db

_SEP = "\x1f"
_REC = "@@@QRREC@@@"


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
                dirnames[:] = []
                continue
            if depth >= max_depth:
                dirnames[:] = []
    return repos


def _repo_key(repo: Path) -> str:
    return hashlib.sha1(str(repo.resolve()).encode()).hexdigest()[:10]


def _collect_repo(
    conn: sqlite3.Connection,
    repo: Path,
    *,
    backfill: bool = False,
    since_ts: int | None = None,
) -> int:
    key = f"git_last_ts:{_repo_key(repo)}"
    if backfill:
        conn.execute("DELETE FROM events WHERE uid LIKE ?", (f"git:{_repo_key(repo)}:%",))
        db.set_state(conn, key, "0")
    last_ts = int(db.get_state(conn, key, "0") or "0")
    fmt = f"{_REC}%H{_SEP}%at{_SEP}%an{_SEP}%s{_SEP}%b"
    cmd = [
        "git", "-C", str(repo), "log", "--no-merges", "--numstat",
        "--date=unix", f"--pretty=format:{fmt}",
    ]
    if since_ts:
        cmd.append(f"--since={since_ts}")
    elif not backfill:
        cmd.extend(["-n", "500"])
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (subprocess.SubprocessError, OSError):
        return 0
    if out.returncode != 0 or not out.stdout:
        return 0

    project = repo.name
    new = 0
    max_ts = last_ts if not backfill else since_ts or 0
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
        if not backfill and ts <= last_ts:
            continue
        if since_ts and ts < since_ts:
            continue
        files = [ln for ln in rest.splitlines() if ln.strip() and "\t" in ln]
        added = deleted = 0
        for ln in files:
            parts = ln.split("\t")
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                added += int(parts[0])
                deleted += int(parts[1])
        files_txt = "\n".join(files[:50])
        diff_line = f"变更统计: +{added} -{deleted} 行"
        exts: set[str] = set()
        for ln in files:
            fp = ln.split("\t", 2)[-1] if "\t" in ln else ln
            if "." in fp:
                exts.add(Path(fp).suffix.lower() or "(noext)")
        semantic = f"涉及类型: {', '.join(sorted(exts)[:12])}" if exts else ""
        content = f"{subject}\n{body}\n\n{diff_line}\n{semantic}\n\n变更文件:\n{files_txt}".strip()
        uid = f"git:{_repo_key(repo)}:{h[:12]}"
        conn.execute("DELETE FROM events WHERE uid=?", (uid,))
        db.insert_event(
            conn,
            uid=uid,
            ts=ts,
            source="git",
            project=project,
            title=subject[:120],
            content=content,
            meta=f'{{"author": "{author}", "files": {len(files)}}}',
        )
        new += 1
        max_ts = max(max_ts, ts)
    db.set_state(conn, key, str(max_ts))
    return new


def collect(
    conn: sqlite3.Connection,
    *,
    backfill: bool = False,
    since_ts: int | None = None,
    roots=None,
) -> int:
    if backfill:
        conn.execute("DELETE FROM events WHERE source='git'")
    scan = roots if roots else config.scan_roots()
    total = 0
    for repo in _find_repos(scan):
        total += _collect_repo(conn, repo, backfill=backfill, since_ts=since_ts)
    return total
