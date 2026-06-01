from __future__ import annotations

import json
import sqlite3
import time

from . import db

_HISTORY_LIMIT = 20


def _title(text: str) -> str:
    line = (text or "").strip().splitlines()[0]
    return line[:120] if line else "(空问题)"


def create_session(conn: sqlite3.Connection, *, title: str, deep: bool, web: bool) -> int:
    ts = db.now()
    cur = conn.execute(
        "INSERT INTO chat_sessions(title, deep, web, created_at, updated_at) VALUES(?,?,?,?,?)",
        (_title(title), int(deep), int(web), ts, ts),
    )
    return int(cur.lastrowid)


def touch_session(conn: sqlite3.Connection, session_id: int) -> None:
    conn.execute(
        "UPDATE chat_sessions SET updated_at=? WHERE id=?",
        (db.now(), session_id),
    )


def get_session(conn: sqlite3.Connection, session_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, title, deep, web, created_at, updated_at FROM chat_sessions WHERE id=?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return _session_row(row)


def _session_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "deep": bool(row["deep"]),
        "web": bool(row["web"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _parse_json(raw: str | None):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def message_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "hits": _parse_json(row["hits"]),
        "web": _parse_json(row["web"]),
        "created_at": row["created_at"],
        "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(row["created_at"])),
    }


def get_messages(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, role, content, hits, web, created_at FROM chat_messages "
        "WHERE session_id=? ORDER BY id",
        (session_id,),
    ).fetchall()
    return [message_row(r) for r in rows]


def history_for_prompt(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT role, content FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
        (session_id, _HISTORY_LIMIT),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def add_user_message(conn: sqlite3.Connection, session_id: int, content: str) -> int:
    cur = conn.execute(
        "INSERT INTO chat_messages(session_id, role, content, created_at) VALUES(?,?,?,?)",
        (session_id, "user", content, db.now()),
    )
    return int(cur.lastrowid)


def add_assistant_message(
    conn: sqlite3.Connection,
    session_id: int,
    content: str,
    *,
    hits: list[dict] | None = None,
    web: list[dict] | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO chat_messages(session_id, role, content, hits, web, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (
            session_id,
            "assistant",
            content,
            json.dumps(hits, ensure_ascii=False) if hits else None,
            json.dumps(web, ensure_ascii=False) if web else None,
            db.now(),
        ),
    )
    return int(cur.lastrowid)


def list_sessions(
    conn: sqlite3.Connection,
    *,
    limit: int = 20,
    page: int = 1,
    date_from: int | None = None,
    date_to: int | None = None,
    q: str | None = None,
) -> tuple[list[dict], int]:
    where: list[str] = []
    args: list = []
    if date_from is not None:
        where.append("s.updated_at>=?")
        args.append(date_from)
    if date_to is not None:
        where.append("s.updated_at<?")
        args.append(date_to)
    if q:
        like = f"%{q}%"
        where.append(
            "(s.title LIKE ? OR EXISTS ("
            "SELECT 1 FROM chat_messages m WHERE m.session_id=s.id AND m.content LIKE ?"
            "))"
        )
        args.extend([like, like])

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) c FROM chat_sessions s{clause}", args).fetchone()["c"]

    offset = (page - 1) * limit
    rows = conn.execute(
        f"SELECT s.id, s.title, s.deep, s.web, s.created_at, s.updated_at, "
        f"(SELECT COUNT(*) FROM chat_messages m WHERE m.session_id=s.id AND m.role='user') AS turns, "
        f"(SELECT content FROM chat_messages m WHERE m.session_id=s.id AND m.role='assistant' "
        f"ORDER BY m.id DESC LIMIT 1) AS last_answer "
        f"FROM chat_sessions s{clause} ORDER BY s.updated_at DESC, s.id DESC LIMIT ? OFFSET ?",
        args + [limit, offset],
    ).fetchall()

    items = []
    for r in rows:
        preview = (r["last_answer"] or r["title"] or "").replace("\n", " ").strip()[:220]
        items.append({
            "id": r["id"],
            "title": r["title"],
            "turns": r["turns"],
            "deep": bool(r["deep"]),
            "web": bool(r["web"]),
            "preview": preview,
            "created": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"])),
            "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["updated_at"])),
        })
    return items, total


def delete_session(conn: sqlite3.Connection, session_id: int) -> bool:
    cur = conn.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
    return cur.rowcount > 0


def find_similar_questions(question: str, limit: int = 3) -> list[dict]:
    q = (question or "").strip()
    if len(q) < 4:
        return []
    like = f"%{q[:40]}%"
    with db.session() as conn:
        rows = conn.execute(
            "SELECT s.id, s.title, s.updated_at FROM chat_sessions s "
            "WHERE s.title LIKE ? OR EXISTS ("
            "SELECT 1 FROM chat_messages m WHERE m.session_id=s.id "
            "AND m.role='user' AND m.content LIKE ?"
            ") ORDER BY s.updated_at DESC LIMIT ?",
            (like, like, limit),
        ).fetchall()
    return [{
        "id": r["id"],
        "title": r["title"],
        "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["updated_at"])),
    } for r in rows]
