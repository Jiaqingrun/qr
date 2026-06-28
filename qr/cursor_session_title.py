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
PREFIX_HINT = "改为 执行- 主题 可进引导语"

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


def prefix_meta_for_chat(
    chat_title: str,
    project: str | None,
    *,
    cfg: dict | None = None,
) -> dict[str, object]:
    """注册项目且侧栏标题无前缀时，写入 events.meta 提醒字段。"""
    from . import workspace

    cfg = cfg or config.load_config()
    pid = workspace.canonical_project_id(project, cfg) if project else ""
    if not pid or not workspace.is_listable_project_id(pid, cfg):
        return {}
    if session_title_policy(chat_title) != "pending":
        return {}
    return {
        "prompt_prefix_pending": True,
        "chat_title": chat_title,
        "prompt_prefix_hint": PREFIX_HINT,
    }


def refresh_prefix_annotations(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    cfg: dict | None = None,
) -> dict[str, int]:
    """按当前侧栏标题刷新近 N 天 cursor 事件的引导语前缀标记。"""
    import json
    from collections import defaultdict

    from . import db, workspace
    from .cursor_prompt_time import parse_event_uid

    cfg = cfg or config.load_config()
    titles = load_session_titles(cfg=cfg)
    since = db.now() - max(1, days) * 86400
    rows = conn.execute(
        "SELECT uid, project, meta FROM events WHERE source='cursor' AND ts>=?",
        (since,),
    ).fetchall()
    by_session: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        parsed = parse_event_uid(row["uid"] or "")
        if parsed:
            by_session[parsed[0]].append(row)

    updated = cleared = 0
    pending_sessions = 0
    for sid, evs in by_session.items():
        chat_title = titles.get(sid, "")
        project = evs[0]["project"]
        pid = workspace.canonical_project_id(project, cfg) or (project or "")
        pending = (
            bool(pid)
            and workspace.is_listable_project_id(pid, cfg)
            and session_title_policy(chat_title) == "pending"
        )
        if pending:
            pending_sessions += 1
        for row in evs:
            try:
                meta = json.loads(row["meta"] or "{}")
            except json.JSONDecodeError:
                meta = {}
            was = bool(meta.get("prompt_prefix_pending"))
            if pending:
                meta["prompt_prefix_pending"] = True
                meta["chat_title"] = chat_title
                meta["prompt_prefix_hint"] = PREFIX_HINT
                if not was:
                    updated += 1
            else:
                changed = False
                if meta.pop("prompt_prefix_pending", None) is not None:
                    changed = True
                meta.pop("chat_title", None)
                meta.pop("prompt_prefix_hint", None)
                if changed:
                    cleared += 1
            conn.execute(
                "UPDATE events SET meta=? WHERE uid=?",
                (json.dumps(meta, ensure_ascii=False), row["uid"]),
            )
    conn.commit()
    return {
        "updated": updated,
        "cleared": cleared,
        "pending_sessions": pending_sessions,
        "days": days,
    }


def count_pending_prefix_sessions(
    conn: sqlite3.Connection,
    *,
    days: int = 7,
    cfg: dict | None = None,
) -> dict[str, int]:
    """统计近 N 天无前缀、本应可进引导语的 Cursor 会话数。"""
    from collections import defaultdict

    from . import db, workspace
    from .cursor_prompt_time import parse_event_uid

    cfg = cfg or config.load_config()
    titles = load_session_titles(cfg=cfg)
    since = db.now() - max(1, days) * 86400
    rows = conn.execute(
        "SELECT uid, project FROM events WHERE source='cursor' AND ts>=?",
        (since,),
    ).fetchall()
    sessions: dict[str, str] = {}
    for row in rows:
        parsed = parse_event_uid(row["uid"] or "")
        if not parsed:
            continue
        sid = parsed[0]
        if sid in sessions:
            continue
        pid = workspace.canonical_project_id(row["project"], cfg) or (row["project"] or "")
        if not pid or not workspace.is_listable_project_id(pid, cfg):
            continue
        if session_title_policy(titles.get(sid, "")) == "pending":
            sessions[sid] = pid
    return {"sessions": len(sessions), "days": days}
