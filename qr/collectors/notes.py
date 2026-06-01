from __future__ import annotations

import hashlib
import json
import sqlite3

from .. import db


def add_note(
    conn: sqlite3.Connection,
    text: str,
    tags: str | None = None,
    *,
    kind: str = "note",
) -> bool:
    ts = db.now()
    h = hashlib.sha1(f"{ts}{text}{kind}".encode("utf-8", "replace")).hexdigest()[:12]
    if kind == "decision":
        title = f"[决策] {text.splitlines()[0][:100]}"
        meta = json.dumps({"tags": tags or "decision", "kind": "decision"}, ensure_ascii=False)
    else:
        title = text.splitlines()[0][:120] if text else "(空笔记)"
        meta = json.dumps({"tags": tags, "kind": kind}, ensure_ascii=False) if tags else None
    return db.insert_event(
        conn, uid=f"note:{kind}:{h}", ts=ts, source="note",
        title=title, content=text, meta=meta,
    )
