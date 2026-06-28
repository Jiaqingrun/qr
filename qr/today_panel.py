"""今日入口：接着干 + 洞察摘要 + 主动提醒。"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import db, digest, proactive, prompt_guides, resume_panel, shell_check, timeutil


def generate(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    own = conn is None
    if own:
        db.init_db()
        conn = db.connect()
    try:
        resume = resume_panel.generate(conn)
        digest_data = digest.generate(days=1)
        alerts = proactive.collect_all()
        pg = prompt_guides.stats(conn)
        from . import cursor_session_title as cst

        prefix_pending = cst.count_pending_prefix_sessions(conn, days=7)
        shell_ts = shell_check.timestamp_stats(days=7)
        return {
            "generated_at": timeutil.format_local(db.now()),
            "active_project": resume.get("active_project", ""),
            "focus_project": resume.get("focus_project"),
            "focus_from_config": resume.get("focus_from_config", False),
            "resume": resume,
            "digest_preview": (digest_data.get("content") or "")[:800],
            "digest_path": digest_data.get("path", ""),
            "alerts": alerts[:10],
            "inbox_count": int(pg.get("inbox", 0)),
            "guides_count": int(pg.get("guides", 0)),
            "pending_prefix_sessions": int(prefix_pending.get("sessions", 0)),
            "pending_prefix_days": int(prefix_pending.get("days", 7)),
            "shell_timestamp": shell_ts,
        }
    finally:
        if own:
            conn.close()
