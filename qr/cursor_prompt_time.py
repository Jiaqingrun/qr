"""从 Cursor agent-transcripts 解析问话的真实/推算时间。"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

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
    base = Path(config.load_config()["cursor_projects_dir"]).expanduser()
    if not base.exists():
        return {}
    return cursor_col._iter_transcripts(base)


def clear_transcript_cache() -> None:
    _transcript_map.cache_clear()


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
