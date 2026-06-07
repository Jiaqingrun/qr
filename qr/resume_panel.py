"""接着干：总览开工卡片数据。"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import config, db, project_brief, prompt_guides, timeutil, workspace


def _open_tasks(tasks: list[dict]) -> list[str]:
    return [str(t.get("text", ""))[:100] for t in tasks if not t.get("done") and t.get("text")][:6]


def _cursor_topics(conn: sqlite3.Connection, project: str | None, limit: int = 6) -> list[dict]:
    since = db.now() - 7 * 86400
    args: list[Any] = [since]
    clause = "source='cursor' AND ts>=?"
    if project:
        clause += " AND (lower(project)=lower(?) OR lower(title) LIKE ?)"
        args.extend([project, f"%{project.split('/')[-1]}%"])
    rows = conn.execute(
        f"SELECT ts, title, project FROM events WHERE {clause} "
        f"ORDER BY ts DESC LIMIT ?",
        (*args, limit),
    ).fetchall()
    return [
        {
            "ts": int(r["ts"]),
            "time": timeutil.format_local(int(r["ts"])),
            "title": r["title"] or "",
            "project": workspace.sanitize_display_project(r["project"]),
        }
        for r in rows
    ]


def _recent_git(conn: sqlite3.Connection, project: str | None, limit: int = 4) -> list[dict]:
    since = db.now() - 14 * 86400
    args: list[Any] = [since]
    clause = "source='git' AND ts>=?"
    if project:
        clause += " AND (lower(project)=lower(?) OR lower(title) LIKE ? OR lower(content) LIKE ?)"
        name = project.split("/")[-1]
        args.extend([project, f"%{name}%", f"%{name}%"])
    rows = conn.execute(
        f"SELECT ts, title, project FROM events WHERE {clause} ORDER BY ts DESC LIMIT ?",
        (*args, limit),
    ).fetchall()
    return [
        {
            "ts": int(r["ts"]),
            "time": timeutil.format_local(int(r["ts"])),
            "title": (r["title"] or "")[:120],
            "project": workspace.sanitize_display_project(r["project"]),
        }
        for r in rows
    ]


def _inbox_preview(conn: sqlite3.Connection, limit: int = 5) -> list[dict]:
    prompt_guides.ensure_schema(conn)
    groups = prompt_guides.list_inbox_groups(conn, limit=limit * 3)
    out: list[dict] = []
    for g in groups.get("groups", [])[:limit]:
        out.append({
            "session_id": g.get("session_id", ""),
            "project": g.get("project", ""),
            "title": (g.get("title") or "")[:80],
            "fragments": int(g.get("fragment_count") or 0),
        })
    return out


def _workspace_open_tasks(conn: sqlite3.Connection | None = None) -> list[dict]:
    items: list[dict] = []
    root = workspace.workspace_root()
    for cat in workspace.categories():
        cat_dir = root / cat
        if not cat_dir.is_dir():
            continue
        for proj in cat_dir.iterdir():
            if not proj.is_dir() or proj.name.startswith("."):
                continue
            pid = workspace.project_id(cat, proj.name)
            readme = proj / "README.md"
            if not readme.is_file():
                continue
            try:
                text = readme.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            tasks = project_brief.parse_readme_tasks(text)
            open_f = _open_tasks(tasks.get("feature_tasks") or [])
            open_o = _open_tasks(tasks.get("opt_tasks") or [])
            pending = open_f + open_o
            if not pending:
                continue
            items.append({"project": pid, "tasks": pending[:4]})
    items.sort(key=lambda x: len(x["tasks"]), reverse=True)
    return items[:4]


def generate(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    own = conn is None
    if own:
        db.init_db()
        conn = db.connect()
    try:
        pid, detected_from = project_brief.detect_active_project(hours=72)
        if not pid:
            pid, detected_from = project_brief.detect_active_project(hours=24 * 14)

        brief = project_brief.brief(pid or "", prefer_detected=not bool(pid))
        if brief.get("error"):
            brief = {"project": "", "lines": []}

        pg = prompt_guides.stats(conn)
        cursor_topics = _cursor_topics(conn, brief.get("project") or pid)
        git_rows = _recent_git(conn, brief.get("project") or pid)
        inbox = _inbox_preview(conn)
        open_tasks = _workspace_open_tasks(conn)

        active_pid = brief.get("project") or pid or ""
        feature_open = _open_tasks(brief.get("feature_tasks") or [])
        opt_open = _open_tasks(brief.get("opt_tasks") or [])

        actions: list[str] = []
        if pg.get("inbox", 0) > 0:
            actions.append(f"引导语收件箱 {pg['inbox']} 条待合并 → 打开「引导语」页")
        if feature_open:
            actions.append(f"README 功能点 {len(feature_open)} 项未完成")
        if cursor_topics:
            actions.append("继续最近 Cursor 话题（见下方列表）")
        if not actions:
            actions.append("可开始新任务，或运行 qr ingest / qr index 同步最新状态")

        return {
            "active_project": active_pid,
            "detected_from": detected_from if pid else brief.get("detected_from", "none"),
            "brief": brief,
            "cursor_topics": cursor_topics,
            "recent_git": git_rows,
            "inbox_count": int(pg.get("inbox", 0)),
            "inbox_preview": inbox,
            "open_tasks": {
                "active": feature_open + opt_open,
                "by_project": open_tasks,
            },
            "actions": actions,
            "generated_at": timeutil.format_local(db.now()),
        }
    finally:
        if own:
            conn.close()
