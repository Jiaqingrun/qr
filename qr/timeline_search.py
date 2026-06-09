"""时间线全文检索（events FTS）。"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from . import db, workspace

_TAG_RE = re.compile(r"<[^>]+>")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5("
        "uid UNINDEXED, source UNINDEXED, project UNINDEXED, title, content, "
        "tokenize='unicode61 remove_diacritics 2'"
        ")"
    )


def _fts_query(text: str) -> str | None:
    words = re.findall(r"[\w\u4e00-\u9fff]{2,}", (text or "").strip())
    if not words:
        return None
    return " ".join(f'"{w}"' for w in words[:12])


def _plain(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def index_event(
    conn: sqlite3.Connection,
    *,
    uid: str,
    source: str,
    project: str | None,
    title: str,
    content: str,
) -> None:
    ensure_schema(conn)
    conn.execute("DELETE FROM events_fts WHERE uid=?", (uid,))
    conn.execute(
        "INSERT INTO events_fts(uid, source, project, title, content) VALUES(?,?,?,?,?)",
        (uid, source, project or "", title or "", _plain(content)),
    )


def remove_event(conn: sqlite3.Connection, uid: str) -> None:
    try:
        conn.execute("DELETE FROM events_fts WHERE uid=?", (uid,))
    except sqlite3.OperationalError:
        pass


def rebuild(conn: sqlite3.Connection) -> int:
    ensure_schema(conn)
    conn.execute("DELETE FROM events_fts")
    rows = conn.execute(
        "SELECT uid, source, project, title, content FROM events"
    ).fetchall()
    for r in rows:
        index_event(
            conn,
            uid=r["uid"],
            source=r["source"],
            project=r["project"],
            title=r["title"] or "",
            content=r["content"] or "",
        )
    return len(rows)


def search(
    conn: sqlite3.Connection,
    q: str,
    *,
    limit: int = 40,
    source: str | None = None,
    project: str | None = None,
    date_from_ts: int | None = None,
    date_to_ts: int | None = None,
) -> list[dict[str, Any]]:
    match = _fts_query(q)
    if not match:
        return []
    ensure_schema(conn)
    try:
        fts_rows = conn.execute(
            "SELECT uid, bm25(events_fts) AS rank FROM events_fts "
            "WHERE events_fts MATCH ? ORDER BY rank LIMIT ?",
            (match, max(limit * 3, 60)),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    proj_cond, proj_args = workspace.events_project_sql_filter()
    hidden = workspace.events_timeline_hidden_sql()
    proj_filter = workspace.project_filter_values(project) if project else []
    out: list[dict[str, Any]] = []
    for fr in fts_rows:
        uid = fr["uid"]
        row = conn.execute(
            f"SELECT uid, ts, source, project, title, content, meta FROM events "
            f"WHERE uid=? AND {proj_cond} AND {hidden}",
            (uid, *proj_args),
        ).fetchone()
        if not row:
            continue
        if source and row["source"] != source:
            continue
        if proj_filter and (row["project"] or "") not in proj_filter:
            continue
        if date_from_ts and int(row["ts"]) < date_from_ts:
            continue
        if date_to_ts and int(row["ts"]) >= date_to_ts:
            continue
        if not workspace.event_row_visible(row["source"], row["project"]):
            continue
        if workspace.event_timeline_hidden(row["source"], row["title"], row["meta"]):
            continue
        out.append({
            "uid": row["uid"],
            "ts": row["ts"],
            "source": row["source"],
            "project": workspace.sanitize_display_project(row["project"]),
            "project_label": workspace.project_timeline_label(row["project"]),
            "title": row["title"],
            "content": row["content"],
            "score": float(-fr["rank"]),
        })
        if len(out) >= limit:
            break
    return out
