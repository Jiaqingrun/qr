"""每月评测：检索基线 + AI 行为快照，写入可对比 Markdown 报告。"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path
from typing import Any

from . import ai_assess, config, eval_suite, timeutil

TEMPLATE_PATH = config.QR_HOME / "templates" / "monthly_eval.md"
OUTPUT_DIR = config.QR_HOME / "eval_monthly"

DEFAULT_TEMPLATE = """# 月度评测 · {{year_month}}

生成时间：{{generated_at}}

> 命令：`qr eval monthly --save` · 对照上月与 `docs/RETRIEVAL_UPGRADE_PLAN.md` 触发条件。

## 一、检索基线（qr eval rag）

| 指标 | 数值 |
|------|------|
| 命中率 | {{retrieval_rate}}%（{{retrieval_ok}}/{{cases}}） |
| 考题泄漏 | {{forbidden_hits}} 题 |
| 均检索耗时 | {{search_avg}}s |

### 未命中 / 泄漏明细

{{rag_failures}}

### extended 扩展题（不阻断发布）

| 指标 | 数值 |
|------|------|
| 命中率 | {{extended_retrieval_rate}}%（{{extended_retrieval_ok}}/{{extended_cases}}） |
| 考题泄漏 | {{extended_forbidden_hits}} 题 |

{{extended_rag_failures}}

> 题集说明见 `docs/EVAL_EXTENDED_CASES.md` · 命令 `qr eval rag --extended`

## 二、AI 使用快照（qr ai-assess）

{{ai_assess_body}}

## 三、四维自评（对照上月，手填或交 Cursor 解读）

| 维度 | 上月 | 本月 | 变化说明 |
|------|------|------|----------|
| 提示工程 | | | |
| 工具链 | | | |
| 复盘习惯 | | | |
| 多项目协作 | | | |

## 四、触发条件检查（RETRIEVAL_UPGRADE_PLAN）

- [ ] 内置检索连续 2 月低于 9/9
- [ ] 索引规模或检索延迟明显恶化
- [ ] 其他：___________

## 五、下月行动项

- [ ] 
"""


def ensure_template() -> Path:
    config.ensure_dirs()
    TEMPLATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not TEMPLATE_PATH.exists():
        TEMPLATE_PATH.write_text(DEFAULT_TEMPLATE, encoding="utf-8")
    return TEMPLATE_PATH


def _run_rag_baseline() -> tuple[list[dict], dict, dict[str, dict]]:
    spec = importlib.util.spec_from_file_location(
        "model_eval", config.REPO_ROOT / "scripts" / "model_eval.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    rows = mod.run_retrieval_baseline(include_extended=True)
    split = eval_suite.summarize_rag_split(rows)
    return rows, split["core"], split


def _rag_failures_text(rows: list[dict], *, tier_group: str | None = None) -> str:
    if tier_group:
        rows = [
            r for r in rows
            if eval_suite.case_tier_group({"tier": r.get("tier", "core")}) == tier_group
        ]
    bad = [
        r for r in rows
        if not r.get("retrieval_ok") or r.get("retrieval_forbidden")
    ]
    if not bad:
        return "（全部通过）"
    lines: list[str] = []
    for r in bad:
        flags = []
        if not r.get("retrieval_ok"):
            flags.append("未命中")
        if r.get("retrieval_forbidden"):
            flags.append("考题泄漏")
        lines.append(f"- {r.get('case')}: {', '.join(flags)}")
    return "\n".join(lines)


def render_report(
    *,
    rag_rows: list[dict],
    rag_summary: dict,
    rag_split: dict[str, dict] | None = None,
    ai_snap: dict[str, Any] | None = None,
    template: str | None = None,
) -> str:
    if ai_snap is None:
        ai_snap = ai_assess.collect_snapshot()
    if template is None:
        template = ensure_template().read_text(encoding="utf-8")
    now = int(ai_snap.get("generated_ts") or time.time())
    year_month = time.strftime("%Y-%m", time.localtime(now))
    ext = (rag_split or {}).get("extended") or {}
    body = template
    replacements = {
        "{{year_month}}": year_month,
        "{{generated_at}}": timeutil.format_local(now),
        "{{retrieval_rate}}": str(rag_summary.get("retrieval_rate", 0)),
        "{{retrieval_ok}}": str(rag_summary.get("retrieval_ok", 0)),
        "{{cases}}": str(rag_summary.get("cases", 0)),
        "{{forbidden_hits}}": str(rag_summary.get("forbidden_hits", 0)),
        "{{search_avg}}": str(rag_summary.get("search_avg", 0)),
        "{{rag_failures}}": _rag_failures_text(rag_rows, tier_group="core"),
        "{{extended_retrieval_rate}}": str(ext.get("retrieval_rate", 0)),
        "{{extended_retrieval_ok}}": str(ext.get("retrieval_ok", 0)),
        "{{extended_cases}}": str(ext.get("cases", 0)),
        "{{extended_forbidden_hits}}": str(ext.get("forbidden_hits", 0)),
        "{{extended_rag_failures}}": _rag_failures_text(rag_rows, tier_group="extended"),
        "{{ai_assess_body}}": ai_assess.format_markdown(ai_snap).strip(),
    }
    for key, val in replacements.items():
        body = body.replace(key, val)
    return body.rstrip() + "\n"


def run_monthly(*, save: bool = True) -> dict[str, Any]:
    """跑检索基线 + AI 快照，可选写入 ~/.qr/eval_monthly/YYYY-MM.md。"""
    from . import db

    db.init_db()
    rag_rows, rag_summary, rag_split = _run_rag_baseline()
    ai_snap = ai_assess.collect_snapshot()
    markdown = render_report(
        rag_rows=rag_rows,
        rag_summary=rag_summary,
        rag_split=rag_split,
        ai_snap=ai_snap,
    )
    year_month = time.strftime("%Y-%m", time.localtime(ai_snap.get("generated_ts") or time.time()))
    out: dict[str, Any] = {
        "year_month": year_month,
        "rag": rag_summary,
        "rag_split": rag_split,
        "ai_assess": {
            "cursor_total": ai_snap.get("cursor_total"),
            "cursor_month_hours": ai_snap.get("cursor_month_hours"),
            "decision_notes": ai_snap.get("decision_notes"),
        },
        "markdown": markdown,
        "template": str(ensure_template()),
    }
    if save:
        config.ensure_dirs()
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUTPUT_DIR / f"{year_month}.md"
        path.write_text(markdown, encoding="utf-8")
        sidecar = OUTPUT_DIR / f"{year_month}.json"
        sidecar.write_text(
            json.dumps(
                {"rag": rag_summary, "rag_split": rag_split, "rag_rows": rag_rows, "ai_assess": ai_snap},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        out["path"] = str(path)
        out["json_path"] = str(sidecar)
    return out
