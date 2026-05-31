from __future__ import annotations

import json
import os
import re
import sqlite3
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


def _parse_transcript(path: Path) -> tuple[str, str] | None:
    user_queries: list[str] = []
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
                        m = _QUERY_RE.search(t)
                        q = m.group(1) if m else _TAG_RE.sub("", t).strip()
                        q = q.strip()
                        if q and not q.startswith("[{"):
                            user_queries.append(q)
                elif role == "assistant":
                    assistant_turns += 1
    except OSError:
        return None
    if not user_queries:
        return None
    title = user_queries[0].splitlines()[0][:120]
    body = "\n\n".join(f"- {q}" for q in user_queries)
    content = f"用户在本次对话中的请求({assistant_turns} 轮助手回复):\n{body}"
    return title, content


def collect(conn: sqlite3.Connection) -> int:
    cfg = config.load_config()
    base = Path(os.path.expanduser(cfg["cursor_projects_dir"]))
    if not base.exists():
        return 0
    new = 0
    for jsonl in base.glob("*/agent-transcripts/*/*.jsonl"):
        uuid = jsonl.stem
        try:
            mt = jsonl.stat().st_mtime
        except OSError:
            continue
        state_key = f"cursor_mtime:{uuid}"
        prev = float(db.get_state(conn, state_key, "0") or "0")
        if mt <= prev:
            continue
        parsed = _parse_transcript(jsonl)
        if parsed is None:
            db.set_state(conn, state_key, repr(mt))
            continue
        title, content = parsed
        project = _clean_project(jsonl.parts[len(base.parts)])
        uid = f"cursor:{uuid}"
        conn.execute("DELETE FROM events WHERE uid=?", (uid,))
        db.insert_event(conn, uid=uid, ts=int(mt), source="cursor",
                        project=project, title=title, content=content)
        db.set_state(conn, state_key, repr(mt))
        new += 1
    return new
