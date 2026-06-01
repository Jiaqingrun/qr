from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from .. import config, db

_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)
_TS_RE = re.compile(r"<timestamp>(.*?)</timestamp>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _texts(message) -> list[str]:
    out: list[str] = []
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                t = item.get("text")
                if t:
                    out.append(t)
    return out


def _clean_project(name: str) -> str:
    parts = name.split("-")
    return parts[-1] if parts else name


def _parse_timestamp(text: str) -> int | None:
    m = _TS_RE.search(text)
    if not m:
        return None
    raw = re.sub(r"\s*\([^)]+\)$", "", m.group(1).strip())
    try:
        dt = datetime.datetime.strptime(raw, "%A, %B %d, %Y, %I:%M %p")
        return int(time.mktime(dt.timetuple()))
    except ValueError:
        return None


def _extract_query(text: str) -> str | None:
    m = _QUERY_RE.search(text)
    q = m.group(1).strip() if m else _TAG_RE.sub("", text).strip()
    if not q or q.startswith("[{"):
        return None
    return q


def _parse_transcript(path: Path) -> list[dict]:
    queries: list[dict] = []
    assistant_turns = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                role = d.get("role")
                msg = d.get("message", {})
                texts = _texts(msg)
                if role == "user":
                    for t in texts:
                        q = _extract_query(t)
                        if not q:
                            continue
                        queries.append({
                            "query": q,
                            "ts": _parse_timestamp(t),
                            "assistant_before": assistant_turns,
                        })
                elif role == "assistant":
                    assistant_turns += 1
    except OSError:
        return []
    return queries


def _iter_transcripts(base: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for jsonl in base.glob("*/agent-transcripts/*/*.jsonl"):
        uuid = jsonl.stem
        try:
            mt = jsonl.stat().st_mtime
        except OSError:
            continue
        prev = found.get(uuid)
        if prev is None or mt > prev.stat().st_mtime:
            found[uuid] = jsonl
    return found


def _resolve_query_ts(queries: list[dict], idx: int, file_mtime: int) -> int:
    ts = queries[idx].get("ts")
    if ts:
        return ts
    prev_i = prev_ts = None
    for j in range(idx - 1, -1, -1):
        if queries[j].get("ts"):
            prev_i, prev_ts = j, queries[j]["ts"]
            break
    next_i = next_ts = None
    for j in range(idx + 1, len(queries)):
        if queries[j].get("ts"):
            next_i, next_ts = j, queries[j]["ts"]
            break
    if prev_ts and next_ts and next_i != prev_i:
        ratio = (idx - prev_i) / (next_i - prev_i)
        return int(prev_ts + (next_ts - prev_ts) * ratio)
    if prev_ts:
        return prev_ts + (idx - (prev_i or idx)) * 60 + 1
    if next_ts:
        return next_ts - (next_i - idx) * 60 - 1
    return file_mtime - (len(queries) - idx) * 30


def _clear_cursor_state(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM events WHERE source='cursor'")
    conn.execute("DELETE FROM state WHERE key LIKE 'cursor_%'")


def _upsert_query_event(
    conn: sqlite3.Connection,
    *,
    uuid: str,
    idx: int,
    total: int,
    query: str,
    ts: int,
    project: str,
) -> None:
    uid = f"cursor:{uuid}:q{idx}"
    title = query.splitlines()[0][:120]
    if total > 1:
        content = f"[对话 {uuid[:8]} · 第 {idx + 1}/{total} 问]\n{query}"
    else:
        content = query
    conn.execute("DELETE FROM events WHERE uid=?", (uid,))
    db.insert_event(
        conn,
        uid=uid,
        ts=ts,
        source="cursor",
        project=project,
        title=title,
        content=content,
        meta=json.dumps({"transcript": uuid, "query_index": idx}, ensure_ascii=False),
    )


def collect(
    conn: sqlite3.Connection,
    *,
    backfill: bool = False,
    since_ts: int | None = None,
    roots=None,
) -> int:
    cfg = config.load_config()
    base = Path(os.path.expanduser(cfg["cursor_projects_dir"]))
    if not base.exists():
        return 0

    if backfill:
        _clear_cursor_state(conn)

    new = 0
    for uuid, jsonl in _iter_transcripts(base).items():
        try:
            data = jsonl.read_bytes()
            file_mtime = int(jsonl.stat().st_mtime)
        except OSError:
            continue

        sig = hashlib.sha256(data).hexdigest()
        state_key = f"cursor_sig:{uuid}"
        if not backfill and db.get_state(conn, state_key) == sig:
            continue

        queries = _parse_transcript(jsonl)
        project = _clean_project(jsonl.parts[len(base.parts)])

        # 移除旧版「整段对话一条」的记录
        conn.execute("DELETE FROM events WHERE uid=?", (f"cursor:{uuid}",))

        if not queries:
            db.set_state(conn, state_key, sig)
            continue

        for i, q in enumerate(queries):
            ts = _resolve_query_ts(queries, i, file_mtime)
            if since_ts and ts < since_ts:
                continue
            _upsert_query_event(
                conn,
                uuid=uuid,
                idx=i,
                total=len(queries),
                query=q["query"],
                ts=ts,
                project=project,
            )
            new += 1

        # 对话被编辑变短时，清理多余的旧问题条目
        stale = conn.execute(
            "SELECT uid FROM events WHERE source='cursor' AND uid LIKE ?",
            (f"cursor:{uuid}:q%",),
        ).fetchall()
        for row in stale:
            try:
                idx = int(row["uid"].rsplit(":q", 1)[-1])
            except ValueError:
                continue
            if idx >= len(queries):
                conn.execute("DELETE FROM events WHERE uid=?", (row["uid"],))

        db.set_state(conn, state_key, sig)

    return new
