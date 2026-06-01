from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

from . import config, db, summary, usage


def generate(days: int = 1) -> dict:
    end = db.now()
    start = end - days * 86400
    with db.session() as conn:
        digest = summary._digest(conn, start, end)
        ev_count = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE ts>=? AND ts<=?", (start, end),
        ).fetchone()["c"]
        by_src = conn.execute(
            "SELECT source, COUNT(*) c FROM events WHERE ts>=? AND ts<=? GROUP BY source",
            (start, end),
        ).fetchall()
        projects = conn.execute(
            "SELECT project, COUNT(*) c FROM events WHERE ts>=? AND ts<=? AND project IS NOT NULL "
            "GROUP BY project ORDER BY c DESC LIMIT 8",
            (start, end),
        ).fetchall()
    apps, total = usage.stats(start, end)
    top_apps = apps[:5] if apps else []
    lines = [
        f"# QR 每日洞察（{time.strftime('%Y-%m-%d', time.localtime(start))} ~ "
        f"{time.strftime('%Y-%m-%d', time.localtime(end))}）",
        "",
        f"- 行为事件：{ev_count} 条",
    ]
    if by_src:
        lines.append("- 来源：" + " · ".join(f"{r['source']}={r['c']}" for r in by_src))
    if projects:
        lines.append("- 活跃项目：" + " · ".join(f"{r['project']}({r['c']})" for r in projects))
    if top_apps:
        lines.append("- 应用 Top：" + " · ".join(f"{a['app']} {a['human']}" for a in top_apps))
    if digest:
        lines.extend(["", "## 摘要", digest[:2000]])
    content = "\n".join(lines)
    config.ensure_dirs()
    out = config.LOGS_DIR / f"digest-{time.strftime('%Y%m%d', time.localtime(end))}.md"
    out.write_text(content, encoding="utf-8")
    return {
        "path": str(out),
        "content": content,
        "events": ev_count,
        "apps": top_apps,
        "projects": [{"name": r["project"], "count": r["c"]} for r in projects],
    }
