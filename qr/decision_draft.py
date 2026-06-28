"""里程碑决策草稿：从 Cursor 对话 + Git 变更生成可编辑模板。"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from . import config, db, workspace
from .cursor_prompt_time import parse_event_uid

_FILE_RE = re.compile(
    r"[\w./-]+\.(?:py|md|ts|tsx|js|jsx|json|yaml|yml|go|rs|swift|kt|java)",
)


def _git_diff_summary(project_dir: Path | None, *, max_stat_lines: int = 15) -> str:
    if not project_dir or not project_dir.is_dir():
        return "（无项目目录，跳过 Git 摘要）"
    git_dir = project_dir / ".git"
    if not git_dir.is_dir():
        return "（项目未初始化 Git）"
    try:
        stat = subprocess.run(
            ["git", "-C", str(project_dir), "diff", "--stat", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if stat.returncode != 0:
            stat = subprocess.run(
                ["git", "-C", str(project_dir), "diff", "--stat"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        body = (stat.stdout or "").strip()
        if not body:
            return "（工作区无未提交变更）"
        lines = body.splitlines()
        if len(lines) > max_stat_lines:
            lines = lines[:max_stat_lines] + [f"… 共 {len(body.splitlines())} 行"]
        return "\n".join(lines)
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"（Git 摘要失败: {e}）"


def _recent_cursor_turns(
    conn,
    *,
    session_id: str | None = None,
    project: str | None = None,
    limit: int = 30,
) -> tuple[str, str, list[dict]]:
    """返回 (session_id, project, turns)。"""
    if session_id:
        rows = conn.execute(
            "SELECT uid, ts, project, title, content FROM events "
            "WHERE source='cursor' AND uid LIKE ? ORDER BY ts DESC LIMIT ?",
            (f"cursor:{session_id}:q%", limit),
        ).fetchall()
        rows = list(reversed(rows))
    elif project:
        pvals = workspace.project_filter_values(project)
        if not pvals:
            pvals = [project]
        ph = ",".join("?" * len(pvals))
        rows = conn.execute(
            f"SELECT uid, ts, project, title, content FROM events "
            f"WHERE source='cursor' AND project IN ({ph}) ORDER BY ts DESC LIMIT ?",
            (*pvals, limit),
        ).fetchall()
        rows = list(reversed(rows))
    else:
        row = conn.execute(
            "SELECT uid FROM events WHERE source='cursor' ORDER BY ts DESC LIMIT 1",
        ).fetchone()
        if not row:
            return "", "", []
        parsed = parse_event_uid(row["uid"])
        if not parsed:
            return "", "", []
        session_id = parsed[0]
        return _recent_cursor_turns(conn, session_id=session_id, limit=limit)

    turns: list[dict] = []
    sid = session_id or ""
    proj = project or ""
    for r in rows:
        parsed = parse_event_uid(r["uid"])
        if parsed:
            sid = sid or parsed[0]
        proj = proj or (r["project"] or "")
        q = (r["title"] or r["content"] or "").strip()
        if q.startswith("Cursor 对话提问"):
            parts = q.split("\n\n", 1)
            q = parts[1].strip() if len(parts) > 1 else q
        turns.append({"ts": r["ts"], "query": q[:500]})

    return sid, proj, turns


def _extract_files(turns: list[dict]) -> list[str]:
    found: set[str] = set()
    for t in turns:
        for m in _FILE_RE.finditer(t.get("query") or ""):
            found.add(m.group(0))
    return sorted(found)[:20]


def build_draft(
    *,
    session_id: str | None = None,
    project: str | None = None,
    turn_limit: int = 30,
) -> dict[str, Any]:
    """生成决策草稿 Markdown（不入库）。"""
    db.init_db()
    with db.session() as conn:
        sid, proj, turns = _recent_cursor_turns(
            conn,
            session_id=session_id,
            project=project,
            limit=max(5, min(turn_limit, 80)),
        )

    proj_dir = workspace.resolve_project_dir(proj) if proj else None
    git_summary = _git_diff_summary(proj_dir)
    files = _extract_files(turns)

    question_lines = ["近期 Cursor 对话围绕以下主题："]
    for i, t in enumerate(turns[-8:], 1):
        q = (t.get("query") or "").splitlines()[0][:160]
        if q:
            question_lines.append(f"{i}. {q}")

    problem_section = (
        question_lines if len(question_lines) > 1 else ["（请描述要解决的问题）"]
    )

    lines = [
        "# 决策记录",
        "",
        "## 问题",
        *problem_section,
        "",
        "## 选项",
        "- 方案 A：…",
        "- 方案 B：…",
        "",
        "## 结论",
        "（待填写）",
        "",
        "## 原因",
        "（待填写）",
        "",
    ]

    if turns:
        lines.extend([
            "## 对话摘要",
            f"- 会话：{sid[:12]}…" if sid else "- 会话：（未知）",
            f"- 项目：{proj or '（未知）'}",
            f"- 近 {len(turns)} 轮问话",
            "",
        ])
        for t in turns[-5:]:
            q = (t.get("query") or "").splitlines()[0][:120]
            if q:
                lines.append(f"- {q}")
        lines.append("")

    if files:
        lines.extend(["## 涉及文件", *[f"- `{f}`" for f in files], ""])

    lines.extend([
        "## Git 变更摘要",
        "```",
        git_summary,
        "```",
        "",
        "> 请编辑后通过 Web 时间线或 `qr log --type decision` 保存。",
    ])

    text = "\n".join(lines)
    return {
        "text": text,
        "session_id": sid,
        "project": proj,
        "turn_count": len(turns),
        "auto_save": False,
    }
