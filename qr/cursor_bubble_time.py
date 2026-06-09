"""从 Cursor state.vscdb 的 bubble createdAt 解析问话精确时间。"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .collectors import cursor as cursor_col

_DEFAULT_STATE_DB = (
    Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
)
_MIN_MATCH_LEN = 8


def state_db_path(cfg: dict | None = None) -> Path:
    cfg = cfg or config.load_config()
    raw = str(cfg.get("cursor_state_db") or "").strip()
    return Path(raw).expanduser() if raw else _DEFAULT_STATE_DB


def _normalize_query(text: str) -> str:
    q = cursor_col.sanitize_user_query(cursor_col._TAG_RE.sub("", (text or "").strip()))
    return re.sub(r"\s+", " ", q)[:240]


def _parse_created_at(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def load_bubble_times(session_id: str, *, cfg: dict | None = None) -> dict[str, int]:
    """问话规范化文本 → unix 时间（同问取最新 bubble）。"""
    path = state_db_path(cfg)
    if not path.is_file():
        return {}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    out: dict[str, int] = {}
    prefix = f"bubbleId:{session_id}:"
    try:
        rows = conn.execute(
            "SELECT value FROM cursorDiskKV WHERE key LIKE ?",
            (prefix + "%",),
        ).fetchall()
    except sqlite3.Error:
        conn.close()
        return {}
    conn.close()
    for (val,) in rows:
        try:
            d = json.loads(val)
        except json.JSONDecodeError:
            continue
        text = (d.get("text") or "").strip()
        ts = _parse_created_at(d.get("createdAt") or "")
        if not text or ts is None or len(text) < 2:
            continue
        nq = _normalize_query(text)
        if len(nq) < _MIN_MATCH_LEN:
            continue
        if nq not in out or ts > out[nq]:
            out[nq] = ts
    return out


def _lookup_ts(nq: str, bubble_map: dict[str, int]) -> int | None:
    if not nq:
        return None
    if nq in bubble_map:
        return bubble_map[nq]
    if len(nq) >= _MIN_MATCH_LEN:
        for key, ts in bubble_map.items():
            if len(key) < _MIN_MATCH_LEN:
                continue
            if nq.startswith(key[: min(60, len(key))]) or key.startswith(nq[: min(60, len(nq))]):
                return ts
    return None


def apply_precise_times(
    session_id: str,
    turns: list[dict],
    *,
    cfg: dict | None = None,
    enabled: bool | None = None,
) -> int:
    """用 state.vscdb 覆盖插值时间；返回修正条数。"""
    cfg = cfg or config.load_config()
    if enabled is None:
        enabled = bool(cfg.get("cursor_precise_time", True))
    if not enabled or not turns or not session_id:
        return 0
    bubble_map = load_bubble_times(session_id, cfg=cfg)
    if not bubble_map:
        return 0
    fixed = 0
    for t in turns:
        nq = _normalize_query(t.get("query") or "")
        ts = _lookup_ts(nq, bubble_map)
        if ts is None:
            continue
        t["ts"] = ts
        t["ts_estimated"] = False
        fixed += 1
    return fixed
