"""每日 AI 使用水平快照（行为数据，便于纵向对比）。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import config, db, usage, workspace

ASSESS_DIR = config.QR_HOME / "assessments"


def collect_snapshot() -> dict[str, Any]:
    """从本机 QR 库采集与 AI 使用相关的可量化指标。"""
    now = db.now()
    month_start = now - 30 * 86400
    week_start = now - 7 * 86400

    with db.session() as conn:
        cursor_by_project = dict(
            conn.execute(
                "SELECT project, COUNT(*) FROM events WHERE source='cursor' GROUP BY project"
            ).fetchall()
        )
        cursor_week = dict(
            conn.execute(
                "SELECT project, COUNT(*) FROM events WHERE source='cursor' AND ts>=? "
                "GROUP BY project",
                (week_start,),
            ).fetchall()
        )
        decisions = conn.execute(
            "SELECT COUNT(*) FROM events WHERE source='note' AND content LIKE '%决策记录%'"
        ).fetchone()[0]
        guides = conn.execute("SELECT COUNT(*) FROM prompt_guides").fetchone()[0]
        fragments = conn.execute("SELECT COUNT(*) FROM prompt_guide_fragments").fetchone()[0]
        try:
            proposals = dict(
                conn.execute(
                    "SELECT status, COUNT(*) FROM prompt_guide_proposals GROUP BY status"
                ).fetchall()
            )
        except Exception:
            proposals = {}
        events_total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    _, month_total = usage.stats(month_start, now)
    month_rows, _ = usage.stats(month_start, now)
    cursor_row = next((r for r in month_rows if "ursor" in (r.get("app") or "")), None)

    root = workspace.workspace_root()
    projects: list[str] = []
    for cat in workspace.categories():
        cat_dir = root / cat
        if not cat_dir.is_dir():
            continue
        for p in sorted(cat_dir.iterdir()):
            if p.is_dir() and not p.name.startswith("."):
                projects.append(f"{cat}/{p.name}")

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
        "generated_ts": now,
        "cursor_total": sum(cursor_by_project.values()),
        "cursor_by_project": cursor_by_project,
        "cursor_week_by_project": cursor_week,
        "decision_notes": int(decisions),
        "prompt_guides": int(guides),
        "prompt_fragments": int(fragments),
        "prompt_proposals": proposals,
        "events_total": int(events_total),
        "chunks": int(chunks),
        "screen_month_hours": round(month_total / 3600, 1),
        "cursor_month_hours": round((cursor_row or {}).get("seconds", 0) / 3600, 1),
        "cursor_month_sessions": int((cursor_row or {}).get("sessions", 0)),
        "workspace_projects": projects,
    }


def format_markdown(snap: dict[str, Any]) -> str:
    lines = [
        f"# AI 使用水平 · 每日快照",
        "",
        f"生成时间：{snap.get('generated_at', '')}",
        "",
        "## 使用强度（近 30 天）",
        f"- Cursor 前台：{snap.get('cursor_month_hours', 0)} h（{snap.get('cursor_month_sessions', 0)} 次切入）",
        f"- 屏幕总活跃：{snap.get('screen_month_hours', 0)} h",
        "",
        "## Cursor 对话（按项目）",
    ]
    for proj, n in sorted(
        (snap.get("cursor_by_project") or {}).items(),
        key=lambda x: -x[1],
    ):
        lines.append(f"- {proj or '(empty)'}: {n}")
    lines.extend(
        [
            "",
            "## 沉淀资产",
            f"- 决策笔记：{snap.get('decision_notes', 0)}",
            f"- 引导语 / 片段：{snap.get('prompt_guides', 0)} / {snap.get('prompt_fragments', 0)}",
            f"- 向量块：{snap.get('chunks', 0)}",
            f"- 时间线事件：{snap.get('events_total', 0)}",
            "",
            "## 工作区项目",
            "- " + " · ".join(snap.get("workspace_projects") or []) or "（无）",
            "",
            "> 将本快照与昨日 `~/.qr/assessments/` 对比，或交给 Cursor 做综合解读。",
            "> 完整多框架评测见知识库对话模板 / `docs/`。",
        ]
    )
    return "\n".join(lines) + "\n"


def save_daily_report(*, markdown: str | None = None, snap: dict[str, Any] | None = None) -> Path:
    config.ensure_dirs()
    ASSESS_DIR.mkdir(parents=True, exist_ok=True)
    if snap is None:
        snap = collect_snapshot()
    if markdown is None:
        markdown = format_markdown(snap)
    day = time.strftime("%Y-%m-%d", time.localtime(snap.get("generated_ts") or time.time()))
    path = ASSESS_DIR / f"{day}.md"
    path.write_text(markdown, encoding="utf-8")
    sidecar = ASSESS_DIR / f"{day}.json"
    sidecar.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
