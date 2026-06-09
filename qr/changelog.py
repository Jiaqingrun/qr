"""项目变更简报：Git + Cursor + 文件活动。"""
from __future__ import annotations

import time
from typing import Any

from . import compliance, config, db, facts, timeutil, workspace


def generate(project: str, days: int = 7) -> dict[str, Any]:
    project = workspace.normalize_project_id((project or "").strip())
    if not project:
        return {"error": "project 不能为空"}
    since = db.now() - days * 86400
    lines = [
        f"# 项目变更简报 · {project}",
        f"周期：近 {days} 天（{timeutil.format_local(since)} ~ {timeutil.format_local(db.now())}）",
        "",
    ]

    with db.session() as conn:
        git_rows = conn.execute(
            "SELECT ts, title, content FROM events WHERE source='git' AND ts>=? "
            "AND (lower(project)=lower(?) OR title LIKE ? OR content LIKE ?) "
            "ORDER BY ts DESC LIMIT 25",
            (since, project, f"%{project}%", f"%{project}%"),
        ).fetchall()
        cursor_rows = conn.execute(
            "SELECT ts, title FROM events WHERE source='cursor' AND ts>=? "
            "ORDER BY ts DESC LIMIT 40",
            (since,),
        ).fetchall()
        file_rows = conn.execute(
            "SELECT ts, title FROM events WHERE source='file' AND ts>=? "
            "AND (lower(project)=lower(?) OR title LIKE ?) ORDER BY ts DESC LIMIT 20",
            (since, project, f"%{project}%"),
        ).fetchall()
        note_n = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE source='note' AND ts>=? "
            "AND (title LIKE ? OR content LIKE ?)",
            (since, f"%{project}%", f"%{project}%"),
        ).fetchone()["c"]

    lines.append("## Git 提交")
    if git_rows:
        for r in git_rows[:15]:
            lines.append(f"- {timeutil.format_local(r['ts'])} · {r['title']}")
    else:
        lines.append("- （无）")

    cursor_topics = [
        r["title"] for r in cursor_rows
        if project.lower() in (r["title"] or "").lower()
    ][:10]
    lines.extend(["", "## Cursor 话题"])
    if cursor_topics:
        for t in cursor_topics:
            lines.append(f"- {t}")
    else:
        lines.append("- （无）")

    lines.extend(["", "## 文件变更"])
    if file_rows:
        for r in file_rows[:10]:
            lines.append(f"- {timeutil.format_local(r['ts'])} · {r['title']}")
    else:
        lines.append("- （无）")

    lines.extend(["", "## 笔记", f"- 相关笔记 {note_n} 条"])

    comp = None
    for r in compliance.scan_index_roots():
        if project.lower() in r["path"].lower():
            comp = r
            break
    if comp:
        lines.extend([
            "",
            "## 合规",
            f"- {'通过' if comp['ok'] else '待改进'}",
        ])
        for issue in comp.get("issues", [])[:4]:
            lines.append(f"  - {issue}")

    fl = facts.list_facts(project)[:6]
    if fl:
        lines.extend(["", "## 稳定事实"])
        for f in fl:
            lines.append(f"- {f.get('key')}: {f.get('value')}")

    content = "\n".join(lines)
    config.ensure_dirs()
    stamp = time.strftime("%Y%m%d")
    out = config.LOGS_DIR / f"changelog-{project.replace('/', '-')}-{stamp}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return {"project": project, "days": days, "path": str(out), "content": content}
