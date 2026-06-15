"""Cursor 侧栏对话标题：前缀决定是否进入引导语收件箱。"""
from __future__ import annotations

import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

from . import config
from .cursor_bubble_time import state_db_path

# 见 standards/STANDARDS.md · Cursor 对话标题前缀
PREFIX_EXECUTE = "执行"
PREFIX_REFERENCE = "参考"
KNOWN_INCLUDE_PREFIXES = frozenset({PREFIX_EXECUTE})
KNOWN_EXCLUDE_PREFIXES = frozenset({PREFIX_REFERENCE})

_PREFIX_RE = re.compile(r"^([^-]+)-")


def parse_session_prefix(title: str | None) -> str | None:
    """解析「前缀-」中的前缀；无连字符前缀则返回 None。"""
    if not title:
        return None
    m = _PREFIX_RE.match(title.strip())
    return m.group(1) if m else None


def should_include_in_prompt_guides(title: str | None) -> bool:
    """仅「执行-」标题进入引导语；「参考-」、未加前缀、未知前缀均不进。"""
    prefix = parse_session_prefix(title)
    if prefix is None:
        return False
    if prefix in KNOWN_INCLUDE_PREFIXES:
        return True
    return False


def session_title_policy(title: str | None) -> str:
    """返回策略标签：execute / reference / pending / unknown_prefix。"""
    prefix = parse_session_prefix(title)
    if prefix is None:
        return "pending"
    if prefix in KNOWN_INCLUDE_PREFIXES:
        return "execute"
    if prefix in KNOWN_EXCLUDE_PREFIXES:
        return "reference"
    return "unknown_prefix"


@lru_cache(maxsize=4)
def _load_titles_cached(db_mtime: float, db_path: str) -> dict[str, str]:
    del db_mtime  # cache key only
    path = Path(db_path)
    if not path.is_file():
        return {}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key='composer.composerHeaders'",
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return {}
    if not row:
        return {}
    try:
        raw = row[0]
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    out: dict[str, str] = {}
    for item in data.get("allComposers") or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("composerId") or "").strip()
        name = str(item.get("name") or "").strip()
        if sid and name and sid != "empty-state-draft":
            out[sid] = name
    return out


def load_session_titles(*, cfg: dict | None = None) -> dict[str, str]:
    """composerId → 侧栏标题（来自 Cursor state.vscdb）。"""
    path = state_db_path(cfg)
    try:
        mtime = path.stat().st_mtime if path.is_file() else 0.0
    except OSError:
        mtime = 0.0
    return dict(_load_titles_cached(mtime, str(path)))


def session_title(session_id: str | None, *, cfg: dict | None = None) -> str:
    if not session_id:
        return ""
    return load_session_titles(cfg=cfg).get(session_id, "")
