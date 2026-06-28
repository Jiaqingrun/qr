"""从 Cursor agent-transcripts 解析问话的真实/推算时间。"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import config
from .collectors import cursor as cursor_col

_EVENT_UID_RE = re.compile(r"^cursor:(.+):q(\d+)$")


def parse_event_uid(uid: str) -> tuple[str, int] | None:
    m = _EVENT_UID_RE.match(uid or "")
    if not m:
        return None
    return m.group(1), int(m.group(2))


@lru_cache(maxsize=1)
def _transcript_map() -> dict[str, Path]:
    cfg = config.load_config()
    base = Path(cfg.get("cursor_projects_dir", "~/.cursor/projects")).expanduser()
    if not base.exists():
        return {}
    return cursor_col._iter_transcripts(base)


def clear_transcript_cache() -> None:
    _transcript_map.cache_clear()
    _query_turn_lookup.cache_clear()


def _normalize_query_key(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:240]


_MIN_FUZZY_QUERY_LEN = 12


def _query_keys_match(needle: str, hay: str) -> bool:
    if not needle or not hay:
        return False
    if needle == hay:
        return True
    shorter, longer = (needle, hay) if len(needle) <= len(hay) else (hay, needle)
    if len(shorter) < _MIN_FUZZY_QUERY_LEN:
        return False
    return longer.startswith(shorter) or shorter in longer


@lru_cache(maxsize=1)
def _query_turn_lookup() -> dict[str, dict[str, Any]]:
    """问话文本 → 转录/归档中的 Cursor 回复（跨会话索引）。"""
    from . import cursor_archive

    lookup: dict[str, dict[str, Any]] = {}

    def add(session_id: str, query_index: int, query: str, reply: str, source: str) -> None:
        key = _normalize_query_key(query)
        if not key or key in lookup:
            return
        lookup[key] = {
            "found": True,
            "query": query.strip(),
            "reply": (reply or "").strip(),
            "query_index": query_index,
            "session_id": session_id,
            "source": source,
        }

    for session_id, jsonl in _transcript_map().items():
        if not jsonl.exists():
            continue
        for i, turn in enumerate(cursor_archive.parse_transcript_turns(jsonl)):
            add(
                session_id,
                i,
                turn.get("query") or "",
                turn.get("reply") or "",
                "transcript",
            )

    root = cursor_archive.archive_root()
    if root.is_dir():
        for session_dir in sorted(root.iterdir()):
            if not session_dir.is_dir():
                continue
            session_id = session_dir.name
            archived_jsonl = session_dir / "transcript.jsonl"
            if archived_jsonl.is_file():
                for i, turn in enumerate(cursor_archive.parse_transcript_turns(archived_jsonl)):
                    add(
                        session_id,
                        i,
                        turn.get("query") or "",
                        turn.get("reply") or "",
                        "archive_transcript",
                    )
            for md in sorted(session_dir.glob("q*.md")):
                try:
                    query_index = int(md.stem[1:])
                except ValueError:
                    continue
                row = cursor_archive.read_turn(session_id, query_index)
                if row:
                    add(session_id, query_index, row.get("query") or "", row.get("reply") or "", "archive")

    return lookup


def _resolve_global_query_match(query_text: str) -> dict[str, Any]:
    empty: dict[str, Any] = {"found": False, "query": "", "reply": "", "source": ""}
    needle = _normalize_query_key(query_text)
    if not needle:
        return empty

    lookup = _query_turn_lookup()
    hit = lookup.get(needle)
    if hit:
        return dict(hit)

    for key, row in lookup.items():
        if _query_keys_match(needle, key):
            return dict(row)
    return empty


def _resolve_from_jsonl(session_id: str, query_index: int) -> dict[str, Any]:
    from . import cursor_archive

    jsonl = _transcript_map().get(session_id)
    if not jsonl or not jsonl.exists():
        return {"found": False, "query": "", "reply": "", "source": ""}
    turns = cursor_archive.parse_transcript_turns(jsonl)
    if query_index < 0 or query_index >= len(turns):
        return {"found": False, "query": "", "reply": "", "source": ""}
    t = turns[query_index]
    return {
        "found": True,
        "query": (t.get("query") or "").strip(),
        "reply": (t.get("reply") or "").strip(),
        "query_index": query_index,
        "session_id": session_id,
        "source": "transcript",
    }


def _resolve_from_archive(session_id: str, query_index: int) -> dict[str, Any]:
    from . import cursor_archive

    row = cursor_archive.read_turn(session_id, query_index)
    if not row:
        return {"found": False, "query": "", "reply": "", "source": ""}
    return {
        "found": True,
        "query": row.get("query") or "",
        "reply": row.get("reply") or "",
        "query_index": query_index,
        "session_id": session_id,
        "source": "archive",
    }


def _resolve_by_query_match(session_id: str, query_text: str) -> dict[str, Any]:
    if not query_text:
        return {"found": False, "query": "", "reply": "", "source": ""}
    from . import cursor_archive

    jsonl = _transcript_map().get(session_id)
    if jsonl and jsonl.exists():
        turns = cursor_archive.parse_transcript_turns(jsonl)
        needle = query_text[:240]
        for i, t in enumerate(turns):
            q = (t.get("query") or "").strip()
            if _query_keys_match(_normalize_query_key(query_text), _normalize_query_key(q)):
                return {
                    "found": True,
                    "query": q,
                    "reply": (t.get("reply") or "").strip(),
                    "query_index": i,
                    "session_id": session_id,
                    "source": "transcript_match",
                }
    # 归档目录按序号扫一遍成本高，仅在 jsonl 缺失时尝试常见序号附近
    return {"found": False, "query": "", "reply": "", "source": ""}


def resolve_cursor_turn(
    session_id: str | None,
    query_index: int | None,
    *,
    event_uid: str | None = None,
    query_text: str | None = None,
) -> dict[str, Any]:
    """从 agent-transcripts / 归档 md 读取某次问话及 Cursor 回复。"""
    empty: dict[str, Any] = {"found": False, "query": "", "reply": "", "source": ""}
    parsed = parse_event_uid(event_uid or "")
    if parsed:
        session_id, query_index = parsed
    if not session_id or query_index is None:
        return empty

    idx = int(query_index)
    qt = (query_text or "").strip()
    for resolver in (
        lambda: _resolve_from_jsonl(session_id, idx),
        lambda: _resolve_from_archive(session_id, idx),
        lambda: _resolve_by_query_match(session_id, qt),
        lambda: _resolve_global_query_match(qt),
    ):
        result = resolver()
        if result.get("found"):
            return result

    clear_transcript_cache()
    for resolver in (
        lambda: _resolve_from_jsonl(session_id, idx),
        lambda: _resolve_from_archive(session_id, idx),
        lambda: _resolve_global_query_match(qt),
    ):
        result = resolver()
        if result.get("found"):
            return result
    return empty


def resolve_query_time(session_id: str, query_index: int) -> tuple[int, bool, str | None]:
    """
    返回 (unix_ts, ts_estimated, query_text)。
    ts_estimated=False 表示来自消息内 <timestamp> 标签。
    """
    jsonl = _transcript_map().get(session_id)
    if not jsonl or not jsonl.exists():
        return 0, True, None
    queries = cursor_col._parse_transcript(jsonl)
    if not queries:
        st = jsonl.stat()
        return int(st.st_mtime), True, None
    cursor_col._assign_query_times(queries, jsonl)
    if query_index < 0 or query_index >= len(queries):
        return int(jsonl.stat().st_mtime), True, None
    q = queries[query_index]
    return int(q["ts"]), bool(q.get("ts_estimated")), q.get("query")
