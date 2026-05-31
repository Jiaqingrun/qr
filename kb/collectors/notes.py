from __future__ import annotations

import hashlib
import sqlite3

from .. import db


def add_note(conn: sqlite3.Connection, text: str, project: str | None = None,
             tags: str | None = None) -> bool:
    ts = db.now()
    h = hashlib.sha1(f"{ts}{text}".encode("utf-8", "replace")).hexdigest()[:12]
    title = text.splitlines()[0][:120] if text else "(空笔记)"
    meta = f'{{"tags": "{tags}"}}' if tags else None
    return db.insert_event(conn, uid=f"note:{h}", ts=ts, source="note",
                           project=project, title=title, content=text, meta=meta)
