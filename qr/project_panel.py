from __future__ import annotations

import json
import time
from pathlib import Path

from . import compliance, config, db, facts, query


def _match_project_path(path: str, project: str) -> bool:
    pl = project.lower()
    p = (path or "").lower().replace("_", "-")
    return pl in p or p.endswith(f"/{pl}") or p.startswith(f"cursor-{pl}")


def panel(project: str, days: int = 14) -> dict:
    project = (project or "").strip()
    if not project:
        return {"error": "project 不能为空"}
    since = db.now() - days * 86400

    with db.session() as conn:
        git_rows = conn.execute(
            "SELECT ts, title, content FROM events WHERE source='git' AND ts>=? "
            "AND lower(project)=lower(?) ORDER BY ts DESC LIMIT 20",
            (since, project),
        ).fetchall()
        git_hits = [dict(r) for r in git_rows]
        if not git_hits:
            git_rows = conn.execute(
                "SELECT ts, title, content FROM events WHERE source='git' AND ts>=? "
                "ORDER BY ts DESC LIMIT 40",
                (since,),
            ).fetchall()
            git_hits = [
                dict(r) for r in git_rows
                if _match_project_path(r["title"] or "", project)
                or _match_project_path(r["content"] or "", project)
            ][:20]

        cursor_rows = conn.execute(
            "SELECT ts, title FROM events WHERE source='cursor' AND ts>=? "
            "ORDER BY ts DESC LIMIT 15",
            (since,),
        ).fetchall()
        cursor_topics = [
            r["title"] for r in cursor_rows
            if project.lower() in (r["title"] or "").lower()
        ][:15]

        notes = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE source='note' AND ts>=? AND (title LIKE ? OR content LIKE ?)",
            (since, f"%{project}%", f"%{project}%"),
        ).fetchone()["c"]

        chats = conn.execute(
            "SELECT COUNT(*) c FROM chat_sessions WHERE title LIKE ?",
            (f"%{project}%",),
        ).fetchone()["c"]

    comp = None
    for r in compliance.scan_index_roots():
        if r["path"].split("/")[-1].lower() == project.lower() or project.lower() in r["path"].lower():
            comp = r
            break
    if comp is None:
        from . import workspace

        root = workspace.resolve_project_dir(project)
        if root and root.is_dir():
            comp = compliance.check_project(root)

    facts_list = facts.list_facts(project)[:12]

    sample_q = f"{project} 项目 最近进展 配置"
    try:
        hits = query.search(sample_q, k=5, project=project)
    except Exception:
        hits = []

    return {
        "project": project,
        "window_days": days,
        "git_commits": [
            {
                "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"])),
                "title": r["title"],
                "preview": (r["content"] or "")[:200],
            }
            for r in git_hits[:8]
        ],
        "cursor_topics": cursor_topics,
        "notes_count": notes,
        "chat_sessions": chats,
        "compliance": comp,
        "stable_facts": facts_list,
        "sample_retrieval": hits,
    }
