"""洞察页每日计划：勾选完成状态与可复制命令。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import config, timeutil

PLAN_PATH = config.QR_HOME / "daily_plan.json"

DEFAULT_ITEMS: list[dict[str, str]] = [
    {
        "id": "ai-skill-assess",
        "label": "每日 AI 水平评测",
        "command": "qr ai-assess --save",
        "hint": "生成可对比的行为数据快照（需已 conda activate qr）",
        "cadence": "daily",
    },
    {
        "id": "monthly-eval",
        "label": "每月模型评测",
        "command": "qr eval run",
        "hint": "全量双模型评测；写入 ~/.qr/logs/eval-YYYYMM.md（com.qr.eval 定时任务）",
        "cadence": "monthly",
    },
]


def _today_key(ts: int | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts or time.time()))


def _month_key(ts: int | None = None) -> str:
    return time.strftime("%Y-%m", time.localtime(ts or time.time()))


def _period_key(cadence: str, ts: int | None = None) -> str:
    return _month_key(ts) if cadence == "monthly" else _today_key(ts)


def _merge_default_items(items: list) -> list:
    by_id = {str(x.get("id")): x for x in items if isinstance(x, dict) and x.get("id")}
    out = [dict(x) for x in items if isinstance(x, dict)] if items else []
    for raw in DEFAULT_ITEMS:
        if raw["id"] not in by_id:
            out.append(dict(raw))
    return out


def _load() -> dict[str, Any]:
    config.ensure_dirs()
    if not PLAN_PATH.exists():
        return {"items": [dict(x) for x in DEFAULT_ITEMS], "completions": {}}
    try:
        data = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
    items = data.get("items")
    if not isinstance(items, list) or not items:
        items = [dict(x) for x in DEFAULT_ITEMS]
    else:
        items = _merge_default_items(items)
    completions = data.get("completions")
    if not isinstance(completions, dict):
        completions = {}
    return {"items": items, "completions": completions}


def _save(data: dict[str, Any]) -> None:
    config.ensure_dirs()
    PLAN_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def list_for_date(day: str | None = None) -> dict[str, Any]:
    """返回某日计划项及勾选状态（含按月计项）。"""
    day = (day or _today_key()).strip()
    month = day[:7] if len(day) >= 7 else _month_key()
    data = _load()
    completions = data.get("completions") or {}
    items_out: list[dict[str, Any]] = []
    for raw in data.get("items") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        iid = str(raw["id"])
        cadence = str(raw.get("cadence") or "daily")
        period = month if cadence == "monthly" else day
        item_days = completions.get(iid) if isinstance(completions.get(iid), dict) else {}
        done = bool(item_days.get(period)) if isinstance(item_days, dict) else False
        items_out.append(
            {
                "id": iid,
                "label": str(raw.get("label") or iid),
                "command": str(raw.get("command") or ""),
                "hint": str(raw.get("hint") or ""),
                "cadence": cadence,
                "period": period,
                "done": done,
            }
        )
    if not items_out:
        for raw in DEFAULT_ITEMS:
            cadence = str(raw.get("cadence") or "daily")
            period = month if cadence == "monthly" else day
            items_out.append({**raw, "period": period, "done": False})
    done_n = sum(1 for x in items_out if x["done"])
    return {
        "date": day,
        "month": month,
        "date_label": timeutil.format_local(int(time.mktime(time.strptime(day, "%Y-%m-%d")))),
        "items": items_out,
        "done_count": done_n,
        "total": len(items_out),
    }


def set_done(item_id: str, done: bool, *, day: str | None = None) -> dict[str, Any]:
    iid = (item_id or "").strip()
    if not iid:
        raise ValueError("缺少计划项 id")
    day = (day or _today_key()).strip()
    data = _load()
    items = _merge_default_items(data.get("items") or [])
    data["items"] = items
    known = {str(x.get("id")): x for x in items if isinstance(x, dict)}
    if iid not in known:
        raise ValueError(f"未知计划项: {iid}")
    cadence = str(known[iid].get("cadence") or "daily")
    period = day[:7] if cadence == "monthly" and len(day) >= 7 else day
    completions = data.setdefault("completions", {})
    if not isinstance(completions, dict):
        completions = {}
        data["completions"] = completions
    per = completions.setdefault(iid, {})
    if not isinstance(per, dict):
        per = {}
        completions[iid] = per
    if done:
        per[period] = True
    elif period in per:
        del per[period]
    _save(data)
    return list_for_date(day)
