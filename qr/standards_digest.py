"""从行为事件与 Cursor 归档提炼规范修订素材。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import timeutil

_MAX_EXCERPT_CHARS = 2400
_MAX_TURNS = 22


def project_match_keys(project_id: str) -> set[str]:
    """事件 project 字段与 workspace id 的多种写法。"""
    pid = (project_id or "").strip().strip("/")
    keys: set[str] = {pid}
    if "/" in pid:
        _, name = pid.split("/", 1)
        keys.add(name)
        keys.add(name.replace("_", "-"))
    else:
        keys.add(pid)
    return {k for k in keys if k}


def _read_archived_turn(path_str: str) -> tuple[str, str]:
    p = Path(path_str).expanduser()
    if not p.is_file():
        return "", ""
    text = p.read_text(encoding="utf-8", errors="replace")
    if "\n---\n" in text:
        q, r = text.split("\n---\n", 1)
        return q.strip(), r.strip()
    return text.strip(), ""


def cursor_conversation_excerpts(
    conn: sqlite3.Connection,
    start: int,
    end: int,
    *,
    project: str | None = None,
    limit: int = _MAX_TURNS,
    max_reply_chars: int = _MAX_EXCERPT_CHARS,
    max_question_chars: int = 800,
    char_budget: int | None = None,
) -> str:
    """读取 Cursor 归档问答片段，供规范 AI 修订。"""
    keys = project_match_keys(project) if project else None
    sql = (
        "SELECT ts, project, title, content, meta FROM events "
        "WHERE source='cursor' AND ts>=? AND ts<=? ORDER BY ts DESC LIMIT ?"
    )
    cap = max(limit * 3, 60)
    rows = conn.execute(sql, (start, end, cap)).fetchall()
    lines: list[str] = []
    for r in rows:
        if keys is not None:
            ev_proj = (r["project"] or "").strip()
            if ev_proj not in keys and not any(k in ev_proj for k in keys if len(k) > 2):
                meta = r["meta"] or ""
                try:
                    mobj = json.loads(meta) if meta else {}
                except json.JSONDecodeError:
                    mobj = {}
                archived_proj = str(mobj.get("project") or "")
                if archived_proj not in keys:
                    continue
        path_str = (r["content"] or "").strip()
        q, reply = _read_archived_turn(path_str)
        if not q:
            q = (r["title"] or "").strip()
        if not q:
            continue
        ts = timeutil.format_local(int(r["ts"]))
        block = f"### {ts}\n**问：** {q[:max_question_chars]}"
        if reply:
            block += f"\n\n**答：** {reply[:max_reply_chars]}"
        if char_budget is not None:
            prospective = "\n\n".join(lines + [block]) if lines else block
            if len(prospective) > char_budget:
                if not lines:
                    lines.append(block[:char_budget].rstrip())
                break
        lines.append(block)
        if len(lines) >= limit:
            break
    if not lines:
        return "（该时间范围内无可用 Cursor 对话归档）"
    lines.reverse()
    return "\n\n".join(lines)


def build_revision_context(
    conn: sqlite3.Connection,
    start: int,
    end: int,
    *,
    project: str | None = None,
    include_behavior: bool = True,
    conv_turn_limit: int | None = None,
    conv_char_budget: int | None = None,
    conv_max_reply_chars: int | None = None,
) -> str:
    from . import governance

    parts: list[str] = []
    if include_behavior:
        if project:
            from . import summary

            raw = summary._digest(conn, start, end)
            keys = project_match_keys(project)
            filtered = []
            for line in (raw or "").splitlines():
                if any(k in line for k in keys):
                    filtered.append(line)
            beh = "\n".join(filtered).strip()
        else:
            beh = governance._digest_for_revision(conn, start, end)
        if beh:
            parts.append(f"## 行为摘要\n\n{beh}")
    conv = cursor_conversation_excerpts(
        conn,
        start,
        end,
        project=project,
        limit=conv_turn_limit or _MAX_TURNS,
        max_reply_chars=conv_max_reply_chars or _MAX_EXCERPT_CHARS,
        char_budget=conv_char_budget,
    )
    parts.append(f"## Cursor 对话摘录\n\n{conv}")
    return "\n\n".join(parts)
