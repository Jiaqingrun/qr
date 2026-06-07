"""近期行为摘要：回答「最近做了什么」类问话（不依赖文档 RAG）。"""
from __future__ import annotations

import re
import time

from . import db, summary

_ACTIVITY_RE = re.compile(
    r"("
    r"最近.*?(啥|什么|哪些|些什么)"
    r"|最近(的)?(活动|行为|工作|进展|动态|记录|情况)"
    r"|(今天|今日|这周|本周|近几天|这几天|近来|近期)"
    r"|我做了(啥|什么|哪些)"
    r"|我最近在"
    r"|最近在做什么"
    r"|最近在干啥"
    r"|总结一下最近"
    r"|最近怎么样"
    r"|最近都在"
    r")",
    re.I,
)


def is_activity_question(question: str) -> bool:
    q = (question or "").strip()
    return bool(q and _ACTIVITY_RE.search(q))


def infer_window_days(question: str) -> int:
    q = question or ""
    if any(x in q for x in ("今天", "今日")):
        return 1
    if any(x in q for x in ("这周", "本周")):
        return 7
    if any(x in q for x in ("这个月", "本月")):
        return 30
    if "近几天" in q or "这几天" in q:
        return 3
    return 7


def prompt_block(question: str, *, days: int | None = None) -> str | None:
    if not is_activity_question(question):
        return None
    span = days if days is not None else infer_window_days(question)
    end = db.now()
    start = end - span * 86400
    with db.session() as conn:
        digest = summary._digest(conn, start, end)
        latest = conn.execute(
            "SELECT period, start_ts, end_ts, content FROM summaries "
            "ORDER BY end_ts DESC LIMIT 1"
        ).fetchone()
    if not digest and not latest:
        return None
    parts = [
        f"时间范围：近 {span} 天（"
        f"{time.strftime('%Y-%m-%d', time.localtime(start))} ~ "
        f"{time.strftime('%Y-%m-%d', time.localtime(end))}）",
    ]
    if digest:
        parts.append("【行为采集摘要】\n" + digest[:6000])
    if latest and latest["content"]:
        period = latest["period"] or "周期"
        parts.append(
            f"【已有周期总结 · {period}】\n" + (latest["content"] or "")[:2500]
        )
    return "\n\n".join(parts)
