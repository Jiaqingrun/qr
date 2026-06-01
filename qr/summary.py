from __future__ import annotations

import datetime
import time
from collections import Counter
from pathlib import Path

from . import config, db, governance, usage
from .ollama_client import Ollama

PERIOD_SECONDS = {"day": 86400, "week": 604800}


def _parse_day(s: str) -> datetime.datetime:
    return datetime.datetime.strptime(s, "%Y-%m-%d")


def _day_start(day: datetime.datetime) -> int:
    return int(time.mktime(day.timetuple()))


def _day_end_exclusive(day: datetime.datetime) -> int:
    return int(time.mktime((day + datetime.timedelta(days=1)).timetuple()))


def _window(period: str) -> tuple[int, int]:
    end = db.now()
    if period == "month":
        now = datetime.datetime.fromtimestamp(end)
        first = datetime.datetime(now.year, now.month, 1)
        start = int(time.mktime(first.timetuple()))
    else:
        start = end - PERIOD_SECONDS.get(period, 604800)
    return start, end


def _range_from_dates(date_from: str, date_to: str) -> tuple[int, int, str]:
    d_from = _parse_day(date_from)
    d_to = _parse_day(date_to)
    if d_from > d_to:
        raise ValueError("起始日期不能晚于结束日期")
    start = _day_start(d_from)
    end = _day_end_exclusive(d_to) - 1
    label = f"{date_from}_{date_to}" if date_from != date_to else date_from
    return start, end, label


def _digest(conn, start: int, end: int) -> str:
    sql = (
        "SELECT ts, source, project, title, content FROM events "
        "WHERE ts>=? AND ts<=?"
    )
    args: list = [start, end]
    sql += " ORDER BY ts"
    rows = conn.execute(sql, args).fetchall()
    usage_digest = usage.digest(start, end)
    if not rows and not usage_digest:
        return ""
    if not rows:
        return usage_digest

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

    if usage_digest:
        lines.append("")
        lines.append(usage_digest)

    return "\n".join(lines)


SYSTEM = (
    "你是用户的个人行为分析助手。基于提供的行为数据摘要与个人规范，"
    "生成一份简洁、可执行的周期总结。用简体中文，使用 Markdown。"
)


def generate(
    period: str = "week",
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> Path:
    if date_from and date_to:
        start, end, label = _range_from_dates(date_from, date_to)
        period_key = "range"
        range_text = (
            f"{date_from} ~ {date_to}"
            if date_from != date_to
            else date_from
        )
        period_desc = f"自定义区间 {range_text}"
    else:
        start, end = _window(period)
        period_key = period
        label = time.strftime("%Y-%m-%d", time.localtime(end))
        range_text = time.strftime("%Y-%m-%d", time.localtime(start))
        range_text += f" ~ {time.strftime('%Y-%m-%d', time.localtime(end))}"
        period_desc = f"最近一{period}（{range_text}）"

    with db.session() as conn:
        digest = _digest(conn, start, end)

    if not digest:
        content = f"# 行为总结（{period_desc}）\n\n该时间范围内没有采集到行为数据。"
    else:
        standards = governance.read_standards()
        prompt = (
            f"# 行为数据摘要（{period_desc}）\n{digest}\n\n"
            f"# 我的个人规范\n{standards}\n\n"
            "请输出包含以下小节的 Markdown 总结：\n"
            "## 概览（这段时间主要在做什么）\n"
            "## 应用使用习惯（基于应用时长/占比，指出时间花在哪、是否专注）\n"
            "## 各项目进展\n"
            "## 工具与命令使用习惯\n"
            "## 与 AI 协作的主题\n"
            "## 偏离规范的地方（对照上面的个人规范，指出不符合的行为）\n"
            "## 下个周期的建议（3-5 条可执行）\n"
            "## 待办清单（从以上内容提取 3-8 条可勾选待办，使用 - [ ] 格式）"
        )
        body = Ollama().generate(prompt, system=SYSTEM)
        content = f"# 行为总结（{period_desc}）\n\n{body}\n"

    config.ensure_dirs()
    out = config.SUMMARIES_DIR / f"{period_key}-{label}.md"
    out.write_text(content, encoding="utf-8")
    with db.session() as conn:
        conn.execute(
            "INSERT INTO summaries(period,start_ts,end_ts,content,created_at) VALUES(?,?,?,?,?)",
            (period_key, start, end, content, db.now()),
        )
    return out
