"""Cursor 对话永久归档：纯文本写入 ~/.qr/cursor_chats，时间线按 file 方式打开。"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import config, db, timeutil
from .collectors import cursor as cursor_col

ARCHIVE_VERSION = 2

_TAG_RE = re.compile(r"<[^>]+>")
_REDACTED_LINE = re.compile(r"^\[REDACTED\]\s*$", re.MULTILINE)
_SKIP_REPLY_PREFIXES = (
    "Searching",
    "Read ",
    "Grep ",
    "Glob ",
    "Called ",
    "工具",
)


def archive_root() -> Path:
    root = config.QR_HOME / "cursor_chats"
    root.mkdir(parents=True, exist_ok=True)
    return root


def turn_filename(query_index: int) -> str:
    return f"q{query_index:04d}.md"


def turn_relpath(session_id: str, query_index: int) -> str:
    return f"cursor_chats/{session_id}/{turn_filename(query_index)}"


def turn_path(session_id: str, query_index: int) -> Path:
    return config.QR_HOME / turn_relpath(session_id, query_index)


def _strip_redacted(text: str) -> str:
    text = _REDACTED_LINE.sub("", text or "")
    text = text.replace("[REDACTED]", "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _clean_assistant_chunk(text: str) -> str:
    plain = _strip_redacted(_TAG_RE.sub("", text or "").strip())
    if not plain or plain.startswith("[{"):
        return ""
    if any(plain.startswith(p) for p in _SKIP_REPLY_PREFIXES):
        return ""
    return plain


def _finalize_reply(parts: list[str]) -> str:
    """只保留面向用户的回复，去掉工具链路与 REDACTED。"""
    chunks = [_clean_assistant_chunk(p) for p in parts]
    chunks = [c for c in chunks if c]
    if not chunks:
        return ""
    for c in reversed(chunks):
        if len(c) >= 20:
            return c
    return chunks[-1]


def parse_transcript_turns(path: Path) -> list[dict]:
    turns: list[dict] = []
    reply_parts: list[str] = []
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
                texts = cursor_col._texts(msg)
                if role == "user":
                    for t in texts:
                        q = cursor_col._extract_query(t)
                        if not q:
                            continue
                        if turns:
                            turns[-1]["reply"] = _finalize_reply(reply_parts)
                            reply_parts = []
                        turns.append({
                            "query": q,
                            "reply": "",
                            "ts": timeutil.parse_cursor_timestamp(t),
                        })
                elif role == "assistant":
                    for t in texts:
                        plain = _clean_assistant_chunk(t)
                        if plain:
                            reply_parts.append(plain)
        if turns and reply_parts:
            turns[-1]["reply"] = _finalize_reply(reply_parts)
    except OSError:
        return []
    if turns:
        known_idx = {i for i, t in enumerate(turns) if t.get("ts")}
        cursor_col._assign_query_times(turns, path)
        for i, t in enumerate(turns):
            t["query_index"] = i
            t["ts_estimated"] = i not in known_idx
    return turns


def _format_turn_md(*, query: str, reply: str) -> str:
    """与普通文档类似：提问全文 + 可选回复，便于系统编辑器打开。"""
    q = query.strip()
    lines = [q, ""]
    r = reply.strip()
    if r:
        lines.extend(["---", "", r, ""])
    return "\n".join(lines)


def archive_session(
    session_id: str,
    jsonl: Path,
    *,
    project: str,
) -> int:
    turns = parse_transcript_turns(jsonl)
    if not turns:
        return 0
    dest_dir = archive_root() / session_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(jsonl, dest_dir / "transcript.jsonl")
    except OSError:
        pass
    meta_path = dest_dir / "meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "project": project,
                "source_transcript": str(jsonl),
                "turn_count": len(turns),
                "archive_version": ARCHIVE_VERSION,
                "archived_at": timeutil.format_local(db.now()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    for t in turns:
        idx = int(t["query_index"])
        turn_path(session_id, idx).write_text(
            _format_turn_md(query=t["query"], reply=t.get("reply") or ""),
            encoding="utf-8",
        )
    return len(turns)


def read_turn(session_id: str, query_index: int) -> dict | None:
    path = turn_path(session_id, query_index)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    query, reply = text, ""
    if "\n---\n" in text:
        query, reply = text.split("\n---\n", 1)
        query, reply = query.strip(), reply.strip()
    return {
        "session_id": session_id,
        "query_index": query_index,
        "path": str(path.resolve()),
        "markdown": text,
        "query": query,
        "reply": reply,
    }


def link_for_event(
    uid: str,
    meta: str | None,
    *,
    title: str | None = None,
    content: str | None = None,
) -> dict | None:
    path_str = (content or "").strip()
    if path_str:
        p = Path(path_str).expanduser()
        if p.is_file():
            return {
                "path": str(p.resolve()),
                "label": (title or p.name)[:240],
                "kind": "file",
            }
    from . import cursor_prompt_time as cpt

    parsed = cpt.parse_event_uid(uid or "")
    if not parsed and meta:
        try:
            data = json.loads(meta)
            sid, qidx = data.get("session_id"), data.get("query_index")
            if sid is not None and qidx is not None:
                parsed = (str(sid), int(qidx))
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None
    if not parsed:
        return None
    session_id, qidx = parsed
    p = turn_path(session_id, qidx)
    if not p.is_file():
        return None
    label = (title or "").strip() or p.name
    return {"path": str(p.resolve()), "label": label[:240], "kind": "file"}
