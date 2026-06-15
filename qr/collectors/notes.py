from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from .. import config, db

NOTES_DIR = config.QR_HOME / "notes"


def _prompts_dir() -> Path:
    return Path(config.load_config().get("prompt_guides_dir", str(config.QR_HOME / "prompts"))).expanduser()


def _note_title(text: str, *, kind: str) -> str:
    if kind == "decision":
        return f"[决策] {text.splitlines()[0][:100]}"
    return text.splitlines()[0][:120] if text else "(空笔记)"


def is_cursor_echo(conn: sqlite3.Connection, text: str) -> bool:
    """与 Cursor 问话标题/正文高度重合时，不应记为手写 note。"""
    line = (text or "").strip()
    if len(line) < 12:
        return False
    title = _note_title(line, kind="note")
    probe = title[:80]
    row = conn.execute(
        "SELECT 1 FROM events WHERE source='cursor' AND "
        "(title=? OR title LIKE ? OR content LIKE ?) LIMIT 1",
        (title, probe + "%", f"%{probe[:60]}%"),
    ).fetchone()
    return row is not None


def purge_cursor_duplicate_notes(conn: sqlite3.Connection) -> int:
    """
    清理误标为 note 的 Cursor 问话镜像（历史同步/误点「记录」等）。
    保留 kind=decision 的手动决策记录。
    """
    rows = conn.execute(
        "SELECT uid, title, content, meta FROM events WHERE source='note'",
    ).fetchall()
    drop: list[str] = []
    for row in rows:
        uid = row["uid"] or ""
        meta_raw = row["meta"] or ""
        kind = ""
        if meta_raw:
            try:
                kind = str(json.loads(meta_raw).get("kind") or "")
            except json.JSONDecodeError:
                kind = ""
        if kind in ("file", "decision"):
            continue
        if uid.startswith("note:file:"):
            continue
        title = (row["title"] or "").strip()
        if not title:
            continue
        cur = conn.execute(
            "SELECT 1 FROM events WHERE source='cursor' AND "
            "(title=? OR title LIKE ? OR content LIKE ?) LIMIT 1",
            (title, title[:80] + "%", f"%{title[:60]}%"),
        ).fetchone()
        if cur:
            drop.append(uid)
            continue
        # 旧版 uid（note:纯哈希）且无 meta，标题与 Cursor 一致
        if meta_raw == "" and re.match(r"^note:[a-f0-9]{12}$", uid):
            cur2 = conn.execute(
                "SELECT 1 FROM events WHERE source='cursor' AND title=? LIMIT 1",
                (title,),
            ).fetchone()
            if cur2:
                drop.append(uid)
    if not drop:
        return 0
    ph = ",".join("?" * len(drop))
    conn.execute(f"DELETE FROM events WHERE uid IN ({ph})", drop)
    return len(drop)


def add_note(
    conn: sqlite3.Connection,
    text: str,
    tags: str | None = None,
    *,
    kind: str = "note",
    allow_cursor_echo: bool = False,
) -> bool | str:
    if not allow_cursor_echo and kind == "note" and is_cursor_echo(conn, text):
        return "cursor_echo"
    ts = db.now()
    h = hashlib.sha1(f"{ts}{text}{kind}".encode("utf-8", "replace")).hexdigest()[:12]
    if kind == "decision":
        title = _note_title(text, kind="decision")
        meta = json.dumps({"tags": tags or "decision", "kind": "decision"}, ensure_ascii=False)
    else:
        title = _note_title(text, kind=kind)
        meta = json.dumps({"tags": tags, "kind": kind}, ensure_ascii=False)
    return db.upsert_event(
        conn, uid=f"note:{kind}:{h}", ts=ts, source="note",
        title=title, content=text, meta=meta,
    )


def purge_misclassified_note_events(conn: sqlite3.Connection) -> int:
    """
    移除误写入时间线的「引导语导出」等记录（曾为 note 来源）。
    引导语 Markdown 仅用于检索索引，不应出现在笔记时间线。
    """
    prompts_root = str(_prompts_dir().resolve())
    rows = conn.execute(
        "SELECT uid, meta FROM events WHERE source='note'",
    ).fetchall()
    drop: list[str] = []
    for row in rows:
        uid = row["uid"] or ""
        meta = row["meta"] or ""
        if "prompts" in uid or "/prompts/" in meta or prompts_root in meta:
            drop.append(uid)
            continue
        if uid.startswith("note:file:") and "/prompts/" in meta:
            drop.append(uid)
    if not drop:
        return 0
    ph = ",".join("?" * len(drop))
    conn.execute(f"DELETE FROM events WHERE uid IN ({ph})", drop)
    return len(drop)


def is_manual_timeline_note(uid: str | None, meta: str | None) -> bool:
    """时间线 note 仅展示 qr log / Web 手动记录（kind=note|decision）。"""
    u = (uid or "").strip()
    if u.startswith("note:note:") or u.startswith("note:decision:"):
        return True
    if u.startswith("note:file:"):
        return False
    if meta:
        try:
            kind = str(json.loads(meta).get("kind") or "")
            if kind in ("note", "decision"):
                return True
            if kind == "file":
                return False
        except json.JSONDecodeError:
            pass
    return False


def manual_note_timeline_sql() -> str:
    """SQL：保留非 note 来源，或手动 note/decision。"""
    return (
        "(source != 'note' OR uid GLOB 'note:note:*' OR uid GLOB 'note:decision:*' "
        "OR json_extract(meta, '$.kind') IN ('note', 'decision'))"
    )


def purge_non_manual_note_events(conn: sqlite3.Connection) -> int:
    """移除 ~/.qr/notes 文件同步等非手动 note 时间线条目。"""
    from .. import timeline_search

    rows = conn.execute(
        "SELECT uid, meta FROM events WHERE source='note'",
    ).fetchall()
    drop = [
        r["uid"] for r in rows
        if not is_manual_timeline_note(r["uid"], r["meta"])
    ]
    if not drop:
        return 0
    ph = ",".join("?" * len(drop))
    conn.execute(f"DELETE FROM events WHERE uid IN ({ph})", drop)
    for uid in drop:
        timeline_search.remove_event(conn, uid)
    return len(drop)


def collect(
    conn: sqlite3.Connection,
    *,
    backfill: bool = False,
    since_ts: int | None = None,
    roots=None,
) -> int:
    """清理误写入时间线的 note；不再把 ~/.qr/notes 文件同步进时间线。"""
    purge_misclassified_note_events(conn)
    purge_cursor_duplicate_notes(conn)
    return purge_non_manual_note_events(conn)
