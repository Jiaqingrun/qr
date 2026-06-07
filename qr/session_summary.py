"""Cursor 会话自动摘要 → ~/.qr/notes 笔记事件。"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from . import config, cursor_archive, db

_FILE_RE = re.compile(
    r"[\w./-]+\.(?:py|md|ts|tsx|js|jsx|json|yaml|yml|go|rs|swift|kt|java)",
)


def _note_path(session_id: str) -> Path:
    d = config.QR_HOME / "notes"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"cursor-session-{session_id[:8]}.md"


def _extractive_summary(session_id: str, project: str, turns: list[dict]) -> str:
    lines = [
        f"# Cursor 会话摘要 · {project}",
        f"session: {session_id}",
        "",
        "## 问话",
    ]
    for t in turns[:12]:
        q = (t.get("query") or "").strip()
        if not q:
            continue
        first = q.splitlines()[0][:200]
        lines.append(f"- {first}")
    files: set[str] = set()
    for t in turns:
        for part in (t.get("query") or "", t.get("reply") or ""):
            for m in _FILE_RE.finditer(part):
                f = m.group(0)
                if "/" in f or "." in f:
                    files.add(f)
    if files:
        lines.extend(["", "## 涉及文件", *[f"- `{f}`" for f in sorted(files)[:20]]])
    replies = [t.get("reply", "").strip() for t in turns if t.get("reply")]
    if replies:
        tail = replies[-1].splitlines()
        snippet = "\n".join(tail[:8]).strip()[:1200]
        if snippet:
            lines.extend(["", "## 末轮回复摘要", snippet])
    return "\n".join(lines).strip() + "\n"


def maybe_summarize_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    project: str,
    turns: list[dict],
    force: bool = False,
) -> bool:
    cfg = config.load_config()
    if not cfg.get("session_auto_summary", True):
        return False
    if not turns:
        return False
    state_key = f"session_summary:{session_id}"
    sig = str(len(turns))
    if not force and db.get_state(conn, state_key) == sig:
        return False
    path = _note_path(session_id)
    body = _extractive_summary(session_id, project, turns)
    path.write_text(body, encoding="utf-8")
    db.set_state(conn, state_key, sig)
    uid = f"note:cursor-summary:{session_id}"
    ts = max((int(t.get("ts", 0)) for t in turns), default=db.now())
    meta = json.dumps({"session_id": session_id, "auto_summary": True}, ensure_ascii=False)
    db.upsert_event(
        conn,
        uid=uid,
        ts=ts,
        source="note",
        project=project,
        title=f"Cursor 会话摘要 · {project}",
        content=str(path.resolve()),
        meta=meta,
    )
    from . import timeline_search

    timeline_search.index_event(
        conn,
        uid=uid,
        source="note",
        project=project,
        title=f"Cursor 会话摘要 · {project}",
        content=body,
    )
    return True


def summarize_from_archive(conn: sqlite3.Connection, session_id: str) -> bool:
    meta_file = cursor_archive.archive_root() / session_id / "meta.json"
    if not meta_file.is_file():
        return False
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    project = str(meta.get("project") or "unknown")
    turns = []
    root = cursor_archive.archive_root() / session_id
    for md in sorted(root.glob("q*.md")):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        turns.append({"query": text[:500], "reply": "", "ts": int(md.stat().st_mtime)})
    return maybe_summarize_session(conn, session_id, project=project, turns=turns)
