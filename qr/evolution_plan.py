"""进化计划：可验收项检测 + EVOLUTION_PLAN.md 自动同步。"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import config, db, eval_suite, retrieval_relations

PLAN_PATH = config.REPO_ROOT / "docs" / "EVOLUTION_PLAN.md"
STATE_PATH = config.QR_HOME / "evolution_plan_state.json"

_SPORTS_PROJECT_IDS = (
    "dev/project-sports",
    "dev/sports/project-sports",
    "dev/sports/Raspi",
)
_SPORTS_POLICY_PATHS = (
    config.REPO_ROOT.parent / "sports" / "project-sports" / "docs" / "hebei-policy.md",
)


@dataclass
class EvolutionItem:
    id: str
    num: int
    title: str
    acceptance: str
    check: Callable[[bool], tuple[bool, str]]


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"statuses": {}, "changelog_ids": [], "rag_cache": {}}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"statuses": {}, "changelog_ids": [], "rag_cache": {}}


def _save_state(state: dict[str, Any]) -> None:
    config.ensure_dirs()
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _conda_env_exists(name: str) -> bool:
    conda = shutil.which("conda") or "/opt/anaconda3/bin/conda"
    if not Path(conda).exists():
        return False
    try:
        proc = subprocess.run(
            [conda, "env", "list"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    if proc.returncode != 0:
        return False
    prefix = f"{name} "
    return any(line.startswith(prefix) for line in proc.stdout.splitlines())


def _rag_summary(*, quick: bool, state: dict[str, Any]) -> dict[str, Any]:
    cache = state.get("rag_cache") if isinstance(state.get("rag_cache"), dict) else {}
    now = db.now()
    if quick and cache.get("summary") and now - int(cache.get("ts") or 0) < 7 * 86400:
        return cache["summary"]
    spec = importlib.util.spec_from_file_location(
        "model_eval", config.REPO_ROOT / "scripts" / "model_eval.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    rows = mod.run_retrieval_baseline()
    summary = eval_suite.summarize_rag(rows)
    state["rag_cache"] = {"ts": now, "summary": summary}
    return summary


def _cursor_count_for_sports(conn) -> int:
    total = 0
    for pid in _SPORTS_PROJECT_IDS:
        row = conn.execute(
            "SELECT COUNT(*) FROM events WHERE source='cursor' AND project=?",
            (pid,),
        ).fetchone()
        total += int(row[0] if row else 0)
    return total


def _check_stage_c(quick: bool, state: dict[str, Any]) -> tuple[bool, str]:
    if not hasattr(retrieval_relations, "expand_projects"):
        return False, "缺少 retrieval_relations.expand_projects"
    rag = _rag_summary(quick=quick, state=state)
    ok = rag.get("retrieval_ok") == rag.get("cases") and not rag.get("forbidden_hits")
    if not ok:
        return False, f"RAG {rag.get('retrieval_ok')}/{rag.get('cases')} 泄漏 {rag.get('forbidden_hits')}"
    return True, f"RAG {rag.get('retrieval_rate')}% ({rag.get('retrieval_ok')}/{rag.get('cases')})"


def _check_cross_project(_quick: bool, _state: dict[str, Any]) -> tuple[bool, str]:
    src = (config.REPO_ROOT / "qr" / "query.py").read_text(encoding="utf-8")
    if "retrieval_relations.resolve_cross_project" not in src:
        return False, "query.search 未接入跨项目解析"
    if "retrieval_relations.expand_projects" not in src:
        return False, "query.search 未接入关联项目扩展"
    return True, "query.search 已接入跨项目扩展"


def _check_prompt_loop(_quick: bool, _state: dict[str, Any]) -> tuple[bool, str]:
    cfg = config.load_config()
    if not cfg.get("standards_auto_revise", True):
        return False, "standards_auto_revise=false"
    src = (config.REPO_ROOT / "qr" / "standards_auto.py").read_text(encoding="utf-8")
    if "infer_and_store" not in src:
        return False, "standards_auto 未 infer 项目关系"
    with db.session() as conn:
        from . import project_relations

        project_relations.ensure_schema(conn)
        links = conn.execute("SELECT COUNT(*) FROM project_links").fetchone()[0]
    return True, f"standards-auto 开启；project_links {links} 条"


def _check_sports_cursor(_quick: bool, _state: dict[str, Any]) -> tuple[bool, str]:
    min_events = int(config.load_config().get("evolution_sports_cursor_min", 5))
    db.init_db()
    with db.session() as conn:
        n = _cursor_count_for_sports(conn)
    if n < min_events:
        return False, f"体育相关 Cursor 事件 {n} < {min_events}"
    return True, f"体育相关 Cursor 事件 {n} 条（含 dev/sports/*）"


def _check_sports_infra(_quick: bool, _state: dict[str, Any]) -> tuple[bool, str]:
    policy = next((p for p in _SPORTS_POLICY_PATHS if p.is_file()), None)
    if not policy:
        return False, "hebei-policy.md 未找到"
    if policy.stat().st_size < 200:
        return False, "hebei-policy.md 过短"
    if not _conda_env_exists("sports"):
        return False, "conda 环境 sports 不存在"
    return True, f"conda sports + {policy.name}"


ITEMS: list[EvolutionItem] = [
    EvolutionItem("stage_c", 1, "阶段 C — 项目关系检索", "限定 dev/qr 问关联项目能命中；eval 9/9 不退化", _check_stage_c),
    EvolutionItem("cross_project", 2, "跨项目问答", "问「A 和 B 怎么协作」自动扩展关联项目 chunk", _check_cross_project),
    EvolutionItem("prompt_loop", 3, "引导语 → 规范 → 行为闭环", "合并引导语的项目优先 standards-auto；每周 infer 关系", _check_prompt_loop),
    EvolutionItem("sports_cursor", 4, "project-sports 真实 Cursor 事件", "在 dev/sports/* 工作区采集到 Cursor 事件", _check_sports_cursor),
    EvolutionItem("sports_infra", 5, "project-sports 业务基建", "conda env sports；hebei-policy.md 结构化草案", _check_sports_infra),
]

_STATUS_LABEL = {"done": "已完成", "active": "进行中", "pending": "未开始"}


def evaluate(*, quick: bool = True) -> list[dict[str, Any]]:
    """运行全部验收检测，不修改文件。"""
    state = _load_state()
    prev = state.get("statuses") if isinstance(state.get("statuses"), dict) else {}
    rows: list[dict[str, Any]] = []
    for item in ITEMS:
        prior = prev.get(item.id, "active")
        try:
            passed, detail = item.check(quick, state)
        except Exception as e:
            passed, detail = False, f"检测异常: {e}"
        if prior == "done":
            status = "done"
        elif passed:
            status = "done"
        else:
            status = "active" if prior in ("active", "done") else "pending"
        rows.append({
            "id": item.id,
            "num": item.num,
            "title": item.title,
            "acceptance": item.acceptance,
            "status": status,
            "status_label": _STATUS_LABEL[status],
            "passed": passed,
            "detail": detail,
        })
    if quick:
        _save_state(state)
    else:
        _save_state(state)
    return rows


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _render_plan(rows: list[dict[str, Any]], changelog: list[dict[str, str]]) -> str:
    table_lines = [
        "| # | 方向 | 状态 | 验收 |",
        "|---|------|------|------|",
    ]
    for r in rows:
        table_lines.append(
            f"| {r['num']} | **{r['title']}** | {r['status_label']} | {r['acceptance']} |"
        )
    log_lines = [
        "| 日期 | 项 | 摘要 |",
        "|------|-----|------|",
    ]
    for row in changelog[-20:]:
        log_lines.append(f"| {row['date']} | {row['item']} | {row['summary']} |")

    return f"""# QR 知识库 · 进化计划（执行跟踪）

> 与 [`RETRIEVAL_UPGRADE_PLAN.md`](./RETRIEVAL_UPGRADE_PLAN.md) 互补：本文记录**产品向**优先级与验收，检索细节见后者。
>
> **自动同步**：`qr evolution sync` 或 `qr update` 结束时按验收规则更新状态（仅 **进行中→已完成**，不自动回退）。
> 状态缓存：`~/.qr/evolution_plan_state.json` · 全量 RAG 验收见 `qr evolution sync --full`。

## 优先级（2026-06 起）

{chr(10).join(table_lines)}

## 刻意不做（维持）

- 移动端 / 远程访问
- 阶段 D～F（HyDE、cross-encoder、多向量）— 见 RETRIEVAL 文档触发条件

## 执行后自检

```bash
conda activate qr && pip install -e ~/QR/dev/qr
python3 -m unittest discover -s tests
qr doctor
qr evolution sync --full
```

## 变更记录

{chr(10).join(log_lines)}
"""


def sync(*, quick: bool = True, dry_run: bool = False) -> dict[str, Any]:
    """检测验收并更新 EVOLUTION_PLAN.md。"""
    state = _load_state()
    if not state.get("statuses"):
        state["statuses"] = parse_plan_statuses()
    prev_statuses = dict(state.get("statuses") or {})
    rows = evaluate(quick=quick)
    new_statuses = {r["id"]: r["status"] for r in rows}
    changelog: list[dict[str, str]] = []
    if isinstance(state.get("changelog"), list):
        changelog = [x for x in state["changelog"] if isinstance(x, dict)]

    existing = PLAN_PATH.read_text(encoding="utf-8") if PLAN_PATH.exists() else ""
    chg_ids = set(state.get("changelog_ids") or [])
    for r in rows:
        old = prev_statuses.get(r["id"], "active")
        new = r["status"]
        if old != "done" and new == "done":
            cid = f"{r['id']}:{_today()}"
            if cid not in chg_ids:
                chg_ids.add(cid)
                changelog.append({
                    "date": _today(),
                    "item": r["title"],
                    "summary": f"自动验收通过：{r['detail']}",
                })

    if not changelog and "自动同步" not in existing:
        changelog = [
            {"date": "2026-06-08", "item": "计划", "summary": "创建本文；启动 C + 闭环 + project-sports"},
            {"date": "2026-06-08", "item": "C", "summary": "retrieval_relations + query.search 关联扩展"},
        ]
        for r in rows:
            if r["status"] == "done":
                changelog.append({
                    "date": _today(),
                    "item": r["title"],
                    "summary": f"基线同步：{r['detail']}",
                })
    else:
        for fixed in (
            {"date": "2026-06-08", "item": "计划", "summary": "创建本文；启动 C + 闭环 + project-sports"},
            {"date": "2026-06-08", "item": "C", "summary": "retrieval_relations + query.search 关联扩展"},
        ):
            if fixed not in changelog:
                changelog.insert(0, fixed)

    markdown = _render_plan(rows, changelog)
    changed = markdown != existing or prev_statuses != new_statuses
    if not dry_run and changed:
        PLAN_PATH.write_text(markdown, encoding="utf-8")

    state["statuses"] = new_statuses
    state["changelog"] = changelog[-30:]
    state["changelog_ids"] = sorted(chg_ids)[-50:]
    state["last_sync"] = db.now()
    state["last_quick"] = quick
    if not dry_run:
        _save_state(state)

    promoted = [
        r["title"] for r in rows
        if prev_statuses.get(r["id"], "active") != "done" and r["status"] == "done"
    ]
    return {
        "ok": True,
        "changed": changed,
        "dry_run": dry_run,
        "quick": quick,
        "promoted": promoted,
        "items": rows,
        "path": str(PLAN_PATH),
    }


def parse_plan_statuses() -> dict[str, str]:
    """从当前 markdown 解析状态（兜底）。"""
    if not PLAN_PATH.exists():
        return {}
    text = PLAN_PATH.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for item in ITEMS:
        pat = re.compile(
            rf"\|\s*{item.num}\s*\|\s*\*\*{re.escape(item.title)}\*\*\s*\|\s*(\S+)\s*\|",
        )
        m = pat.search(text)
        if not m:
            continue
        label = m.group(1)
        if label == "已完成":
            out[item.id] = "done"
        elif label == "进行中":
            out[item.id] = "active"
        else:
            out[item.id] = "pending"
    return out
