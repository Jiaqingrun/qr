"""跨来源事件关联：同路径、同 commit、同 Cursor 会话。"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from . import db, workspace

_PATH_RE = re.compile(
    r"(?:/Users/[^\s\"']+|~/?[^\s\"']+|\b[\w./-]+\.(?:py|md|json|ts|tsx|js|jsx|yaml|yml|sh|go|rs)\b)",
)
_COMMIT_RE = re.compile(r"\b[0-9a-f]{7,40}\b", re.I)
_SESSION_RE = re.compile(r"cursor:([0-9a-f-]{36})")


def _extract_keys(source: str, title: str, content: str, meta: str | None, uid: str) -> set[str]:
    keys: set[str] = set()
    text = f"{title or ''}\n{content or ''}"
    for m in _PATH_RE.finditer(text):
        p = m.group(0).replace("\\", "/")
        if len(p) > 6 and not p.startswith("http"):
            keys.add(f"path:{p.lower()}")
    for m in _COMMIT_RE.finditer(text):
        if source == "git" or len(m.group(0)) >= 8:
            keys.add(f"commit:{m.group(0).lower()[:12]}")
    if meta:
        try:
            mobj = json.loads(meta)
            sid = mobj.get("session_id")
            if sid:
                keys.add(f"session:{sid}")
        except json.JSONDecodeError:
            pass
    sm = _SESSION_RE.search(uid or "")
    if sm:
        keys.add(f"session:{sm.group(1)}")
    return keys


def related_for_event(
    conn: sqlite3.Connection,
    *,
    uid: str,
    source: str,
    title: str,
    content: str,
    meta: str | None,
    ts: int,
    limit: int = 8,
    window_hours: int = 72,
) -> list[dict[str, Any]]:
    keys = _extract_keys(source, title, content, meta, uid)
    if not keys:
        return []
    start = ts - window_hours * 3600
    end = ts + window_hours * 3600
    proj_cond, proj_args = workspace.events_project_sql_filter()
    hidden = workspace.events_timeline_hidden_sql()
    rows = conn.execute(
        f"SELECT uid, ts, source, project, title, content, meta FROM events "
        f"WHERE uid!=? AND ts>=? AND ts<=? AND {proj_cond} AND {hidden} "
        f"ORDER BY ABS(ts-?) ASC LIMIT 120",
        (uid, start, end, *proj_args, ts),
    ).fetchall()
    scored: list[tuple[float, dict]] = []
    for r in rows:
        if not workspace.event_row_visible(r["source"], r["project"]):
            continue
        if workspace.event_timeline_hidden(r["source"], r["title"], r["meta"]):
            continue
        rk = _extract_keys(r["source"], r["title"], r["content"], r["meta"], r["uid"])
        overlap = keys & rk
        if not overlap:
            continue
        dt = abs(int(r["ts"]) - ts) / 3600.0
        score = len(overlap) * 10.0 - dt * 0.1
        scored.append((score, {
            "uid": r["uid"],
            "ts": r["ts"],
            "source": r["source"],
            "project": workspace.sanitize_display_project(r["project"]),
            "title": r["title"],
            "match": sorted(overlap)[:3],
        }))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in scored[:limit]]
