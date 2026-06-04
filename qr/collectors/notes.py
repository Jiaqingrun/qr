from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from .. import config, db

NOTES_DIR = config.QR_HOME / "notes"
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*", re.DOTALL)
_FM_DATE_RE = re.compile(
    r"^(?:date|created|time)\s*:\s*(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


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
    保留 kind=file（~/.qr/notes）与 decision。
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


def _file_uid(path: Path) -> str:
    rel = str(path.resolve())
    h = hashlib.sha1(rel.encode("utf-8", "replace")).hexdigest()[:16]
    return f"note:file:{h}"


def _parse_frontmatter_ts(raw: str) -> int | None:
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return None
    dm = _FM_DATE_RE.search(m.group(1))
    if not dm:
        return None
    s = dm.group(1).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            import datetime

            dt = datetime.datetime.strptime(s, fmt)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


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


def _sync_file(conn: sqlite3.Connection, path: Path) -> bool:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
        st = path.stat()
    except OSError:
        return False
    if not raw:
        return False
    if not path.resolve().is_relative_to(NOTES_DIR.resolve()):
        return False

    uid = _file_uid(path)
    row = conn.execute(
        "SELECT ts, content FROM events WHERE uid=?", (uid,),
    ).fetchone()
    if row:
        ts = int(row["ts"])
    else:
        ts = _parse_frontmatter_ts(raw) or int(st.st_mtime)

    title = raw.splitlines()[0][:120] if raw else path.stem
    if title.startswith("#"):
        title = title.lstrip("#").strip()[:120]
    meta = json.dumps(
        {"kind": "file", "path": str(path), "mtime": int(st.st_mtime)},
        ensure_ascii=False,
    )
    db.upsert_event(
        conn,
        uid=uid,
        ts=ts,
        source="note",
        title=title,
        content=raw,
        meta=meta,
    )
    return True


def _prompts_dir() -> Path:
    return Path(config.load_config().get("prompt_guides_dir", str(config.QR_HOME / "prompts"))).expanduser()


def collect(
    conn: sqlite3.Connection,
    *,
    backfill: bool = False,
    since_ts: int | None = None,
    roots=None,
) -> int:
    """仅同步 ~/.qr/notes/*.md 到时间线（不含 ~/.qr/prompts 引导语导出）。"""
    purge_misclassified_note_events(conn)
    purge_cursor_duplicate_notes(conn)
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = sorted(NOTES_DIR.glob("*.md"))
    n = 0
    for path in paths:
        try:
            mt = int(path.stat().st_mtime)
        except OSError:
            continue
        if since_ts and mt < since_ts:
            continue
        if _sync_file(conn, path):
            n += 1
    return n
