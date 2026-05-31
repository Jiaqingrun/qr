from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

from . import config, db, governance
from .ollama_client import Ollama

PERIOD_SECONDS = {"day": 86400, "week": 604800, "month": 2592000}


def _window(period: str) -> tuple[int, int]:
    end = db.now()
    start = end - PERIOD_SECONDS.get(period, 604800)
    return start, end


def _digest(conn, start: int, end: int) -> str:
    rows = conn.execute(
        "SELECT ts, source, project, title, content FROM events "
        "WHERE ts>=? AND ts<=? ORDER BY ts", (start, end)
    ).fetchall()
    if not rows:
        return ""

    by_source = Counter(r["source"] for r in rows)
    lines = [f"事件总数: {len(rows)}",
             "按来源: " + ", ".join(f"{k}={v}" for k, v in by_source.items()), ""]

    shell = [r["title"] for r in rows if r["source"] == "shell"]
    if shell:
        top = Counter(c.split()[0] for c in shell if c.split()).most_common(12)
        lines.append("【Shell 高频命令】 " + ", ".join(f"{c}×{n}" for c, n in top))

    git = [r for r in rows if r["source"] == "git"]
    if git:
        lines.append(f"【Git 提交】共 {len(git)} 次:")
        proj = Counter(r["project"] for r in git)
        for p, n in proj.most_common():
            subjects = [r["title"] for r in git if r["project"] == p][:8]
            lines.append(f"  - {p} ({n}): " + "; ".join(subjects))

    cursor = [r["title"] for r in rows if r["source"] == "cursor"]
    if cursor:
        lines.append(f"【AI 协作主题】共 {len(cursor)} 段对话:")
        for t in cursor[:15]:
            lines.append(f"  - {t}")

    files = [r for r in rows if r["source"] == "file"]
    if files:
        proj = Counter(r["project"] for r in files)
        lines.append("【改动的项目】 " + ", ".join(f"{p}({n}文件)" for p, n in proj.most_common(15)))

    notes = [r["content"] for r in rows if r["source"] == "note"]
    if notes:
        lines.append("【手动笔记】")
        for n in notes:
            lines.append(f"  - {n}")

    return "\n".join(lines)


SYSTEM = (
    "你是用户的个人行为分析助手。基于提供的行为数据摘要与个人规范，"
    "生成一份简洁、可执行的周期总结。用简体中文，使用 Markdown。"
)


def generate(period: str = "week") -> Path:
    start, end = _window(period)
    with db.session() as conn:
        digest = _digest(conn, start, end)
    label = time.strftime("%Y-%m-%d", time.localtime(end))
    if not digest:
        content = f"# 行为总结 ({period}, 截至 {label})\n\n该周期内没有采集到行为数据。"
    else:
        standards = governance.read_standards()
        prompt = (
            f"# 行为数据摘要（最近一个{period}）\n{digest}\n\n"
            f"# 我的个人规范\n{standards}\n\n"
            "请输出包含以下小节的 Markdown 总结：\n"
            "## 概览（这段时间主要在做什么）\n"
            "## 各项目进展\n"
            "## 工具与命令使用习惯\n"
            "## 与 AI 协作的主题\n"
            "## 偏离规范的地方（对照上面的个人规范，指出不符合的行为）\n"
            "## 下个周期的建议（3-5 条可执行）"
        )
        body = Ollama().generate(prompt, system=SYSTEM)
        content = f"# 行为总结 ({period}, 截至 {label})\n\n{body}\n"

    config.ensure_dirs()
    out = config.SUMMARIES_DIR / f"{period}-{label}.md"
    out.write_text(content, encoding="utf-8")
    with db.session() as conn:
        conn.execute(
            "INSERT INTO summaries(period,start_ts,end_ts,content,created_at) VALUES(?,?,?,?,?)",
            (period, start, end, content, db.now()),
        )
    return out
