from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any

from . import config, db, facts, workspace

_COMPLETION_KEYS = frozenset(
    {"completion", "progress", "完成度", "status", "项目状态"}
)
_APPROVED_KEYS = frozenset(
    {"completion_approved", "project_approved", "approved", "认可完成", "已认可", "项目认可"}
)
_FEATURES_COMPLETE_KEYS = frozenset(
    {"features_complete", "功能点完成", "功能已完成", "features_done"}
)
_YES = frozenset({"1", "true", "yes", "是", "y", "已认可", "完成", "done", "approved", "已归档"})
_FEATURE_HEADINGS = (
    "功能", "特性", "能力", "features", "feature", "核心能力", "主要功能",
    "功能点", "功能清单", "设计功能", "需求", "requirements", "里程碑",
)
_OPT_HEADINGS = ("优化", "优化项", "打磨", "polish", "refinement", "完善")
_TODO_HEADINGS = ("待办", "todo", "roadmap", "计划", "plan")
_PURPOSE_HEADINGS = ("用途", "目的", "简介", "概述", "about", "项目说明", "背景")
_TASK_LINE = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.+)$")
_COMPLETION_PCT = re.compile(
    r"(?:完成度|进度|progress|completion)[:：\s]*(\d{1,3})\s*%?",
    re.I,
)
_APPROVED_LINE = re.compile(
    r"(?:认可完成|项目认可|completion_approved)[:：\s]*(是|yes|true|已认可|完成)",
    re.I,
)
_STATUS_DONE = re.compile(r"状态[:：\s]*已(完成|归档|认可|结项)", re.I)


def _strip_md(s: str) -> str:
    t = re.sub(r"`([^`]+)`", r"\1", s)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"[*_#>]+", "", t).strip()
    return t


def _section_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    blocks: list[tuple[str, list[str]]] = []
    cur_head = ""
    cur: list[str] = []
    for line in lines:
        if re.match(r"^#{1,3}\s+", line):
            if cur_head or cur:
                blocks.append((cur_head, cur))
            cur_head = _strip_md(line.lstrip("#").strip()).lower()
            cur = []
        else:
            cur.append(line)
    if cur_head or cur:
        blocks.append((cur_head, cur))
    return blocks


def _heading_match(head: str, keywords: tuple[str, ...]) -> bool:
    h = head.lower()
    return any(k.lower() in h for k in keywords)


def _tasks_from_body(body: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in body:
        m = _TASK_LINE.match(line)
        if m:
            out.append({"done": m.group(1).lower() == "x", "text": _strip_md(m.group(2))[:160]})
    return out


def _bullets_from_lines(body: list[str], limit: int = 6) -> list[str]:
    out: list[str] = []
    for line in body:
        m = _TASK_LINE.match(line)
        if m:
            out.append(_strip_md(m.group(2))[:120])
        elif re.match(r"^\s*[-*]\s+", line):
            out.append(_strip_md(re.sub(r"^\s*[-*]\s+", "", line))[:120])
        elif re.match(r"^\s*\d+\.\s+", line):
            out.append(_strip_md(re.sub(r"^\s*\d+\.\s+", "", line))[:120])
        if len(out) >= limit:
            break
    return [x for x in out if x]


def _first_paragraph(lines: list[str], start: int = 0) -> str:
    buf: list[str] = []
    for line in lines[start:]:
        s = line.strip()
        if not s:
            if buf:
                break
            continue
        if s.startswith("#"):
            break
        if s.startswith("```"):
            break
        buf.append(_strip_md(s))
    return " ".join(buf)[:280]


def parse_readme_tasks(text: str) -> dict[str, list[dict[str, Any]]]:
    """从 README 解析功能点 / 优化项 checklist。"""
    lines = text.splitlines()
    feature_tasks: list[dict[str, Any]] = []
    opt_tasks: list[dict[str, Any]] = []
    for head, body in _section_blocks(lines):
        if _heading_match(head, _FEATURE_HEADINGS):
            feature_tasks.extend(_tasks_from_body(body))
        elif _heading_match(head, _OPT_HEADINGS):
            opt_tasks.extend(_tasks_from_body(body))
        elif _heading_match(head, _TODO_HEADINGS) and not feature_tasks:
            feature_tasks.extend(_tasks_from_body(body))
    if not feature_tasks:
        all_tasks = _tasks_from_body(lines)
        if len(all_tasks) >= 2:
            feature_tasks = all_tasks
    return {"feature_tasks": feature_tasks, "opt_tasks": opt_tasks}


def parse_readme(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    title = ""
    purpose = ""
    features: list[str] = []
    tasks = parse_readme_tasks(text)

    for i, line in enumerate(lines):
        if line.startswith("# ") and not title:
            title = _strip_md(line[2:].strip())
            purpose = _first_paragraph(lines, i + 1)
            break

    for head, body in _section_blocks(lines):
        if _heading_match(head, _FEATURE_HEADINGS):
            features.extend(_bullets_from_lines(body))
        elif _heading_match(head, _PURPOSE_HEADINGS) and not purpose:
            purpose = _first_paragraph(body) or purpose

    if not features:
        for t in tasks["feature_tasks"]:
            if t.get("text"):
                features.append(str(t["text"]))

    return {
        "title": title,
        "purpose": purpose,
        "features": features[:8],
        "feature_tasks": tasks["feature_tasks"],
        "opt_tasks": tasks["opt_tasks"],
    }


def _fact_truthy(val: str) -> bool:
    return str(val or "").strip().lower() in _YES


def _is_approved(project: str, readme_text: str) -> bool:
    for f in facts.list_facts(project):
        key = str(f.get("key") or "").lower()
        if key in _APPROVED_KEYS and _fact_truthy(str(f.get("value") or "")):
            return True
    if readme_text:
        if _APPROVED_LINE.search(readme_text) or _STATUS_DONE.search(readme_text):
            return True
    return False


def _features_marked_complete(project: str) -> bool:
    for f in facts.list_facts(project):
        key = str(f.get("key") or "").lower()
        if key in _FEATURES_COMPLETE_KEYS and _fact_truthy(str(f.get("value") or "")):
            return True
    return False


def set_completion_approved(project: str, approved: bool = True) -> dict:
    pid = workspace.normalize_project_id(project.strip())
    row = facts.add_fact(
        "completion_approved",
        "true" if approved else "false",
        project=pid,
        source="user",
    )
    return {"ok": True, "project": pid, "approved": approved, "fact": row}


def _git_last_ts(proj_dir: Path) -> int:
    try:
        proc = subprocess.run(
            ["git", "-C", str(proj_dir), "log", "-1", "--format=%ct"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip().isdigit():
            return int(proc.stdout.strip())
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return 0


def _last_activity_ts(conn, project: str, proj_dir: Path | None) -> int:
    name = project.split("/")[-1].lower()
    row = conn.execute(
        "SELECT MAX(ts) m FROM events WHERE lower(project)=lower(?) "
        "OR lower(title) LIKE ? OR lower(content) LIKE ?",
        (project, f"%{name}%", f"%{name}%"),
    ).fetchone()
    ts = int(row["m"] or 0)
    doc = conn.execute(
        "SELECT MAX(mtime) m FROM documents WHERE lower(project)=lower(?) "
        "OR lower(path) LIKE ?",
        (project, f"%/{name}/%"),
    ).fetchone()
    ts = max(ts, int(doc["m"] or 0))
    if proj_dir and proj_dir.is_dir():
        ts = max(ts, _git_last_ts(proj_dir))
        try:
            ts = max(ts, int(proj_dir.stat().st_mtime))
        except OSError:
            pass
    return ts


def _task_stats(tasks: list[dict[str, Any]]) -> tuple[int, int, bool]:
    total = len(tasks)
    if total == 0:
        return 0, 0, True
    done = sum(1 for t in tasks if t.get("done"))
    return done, total, done >= total


def evaluate_completion(
    project: str,
    readme_text: str,
    feature_tasks: list[dict[str, Any]],
    opt_tasks: list[dict[str, Any]],
    *,
    last_activity_ts: int,
    dormant_days: int,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    完成度规则（100% 须同时满足）：
    1. 设计功能点 checklist 全部 [x]（或稳定事实 features_complete）
    2. 若有「优化」清单则全部 [x]
    3. 距末次修改 ≥ completion_dormant_days
    4. 用户认可（稳定事实 completion_approved 或 README 认可完成）
    """
    cfg = cfg or config.load_config()
    dormant_days = int(cfg.get("completion_dormant_days", 14))
    now = db.now()
    days_since = (
        max(0, (now - last_activity_ts) // 86400) if last_activity_ts else 999
    )
    dormant = days_since >= dormant_days if last_activity_ts else False

    f_done, f_total, features_ok = _task_stats(feature_tasks)
    if _features_marked_complete(project):
        features_ok = True
        if f_total == 0:
            f_done, f_total = 1, 1

    o_done, o_total, opt_ok = _task_stats(opt_tasks)
    opt_required = o_total > 0

    approved = _is_approved(project, readme_text)
    gates = {
        "features_done": features_ok and f_total > 0,
        "features": {"done": f_done, "total": f_total},
        "optimization_done": opt_ok if opt_required else True,
        "optimization": {"done": o_done, "total": o_total, "required": opt_required},
        "dormant": dormant,
        "dormant_days": days_since,
        "dormant_required": dormant_days,
        "approved": approved,
    }

    hints: list[str] = []
    if f_total == 0 and not _features_marked_complete(project):
        hints.append("在 README 写「## 功能点」并用 - [ ] / - [x] 列出设计项")
        pct = None
        label = "待定义功能点"
    elif not features_ok:
        hints.append(f"功能点 {f_done}/{f_total}")
        pct = int(round(100 * f_done / f_total * 0.85)) if f_total else 0
        pct = min(pct, 84)
        label = f"{pct}%"
    else:
        hints.append("功能点已全部实现")
        pct = 85
        label = "85%"

    if opt_required and not opt_ok:
        hints.append(f"优化项 {o_done}/{o_total}")
        pct = min(pct or 84, 88) if pct is not None else 88
        label = f"{pct}%"
    elif opt_required and opt_ok:
        hints.append("优化项已完成")
        if pct is not None and pct < 90:
            pct = 90
            label = "90%"

    if not dormant:
        hints.append(f"距上次修改 {days_since} 天（需 ≥{dormant_days} 天无改动）")
        if pct is not None:
            pct = min(pct, 94)
            label = f"{pct}%"
    else:
        hints.append(f"已稳定 {days_since} 天未修改")
        if pct is not None and pct < 96:
            pct = 96
            label = "96%"

    if not approved:
        hints.append("待认可完成（项目页可点「认可完成」）")
        if pct is not None:
            pct = min(pct, 99)
            label = f"{pct}%"
    else:
        hints.append("已认可")

    complete = (
        gates["features_done"]
        and gates["optimization_done"]
        and gates["dormant"]
        and gates["approved"]
    )
    if complete:
        pct = 100
        label = "100% · 已认可完成"
        hints = [
            "设计功能点已全部实现",
            "优化项已完成" if opt_required else "无待办优化项",
            f"已稳定 {days_since} 天未修改",
            "已认可结项",
        ]

    return {
        "pct": pct,
        "label": label,
        "hint": " · ".join(hints),
        "source": "completion_model",
        "complete": complete,
        "gates": gates,
    }


def detect_active_project(hours: int = 8) -> tuple[str | None, str]:
    since = db.now() - hours * 3600
    with db.session() as conn:
        row = conn.execute(
            "SELECT project, MAX(ts) last_ts, COUNT(*) c FROM events "
            "WHERE ts>=? AND project IS NOT NULL AND trim(project)!='' "
            "GROUP BY lower(project) ORDER BY last_ts DESC LIMIT 1",
            (since,),
        ).fetchone()
        if row and row["project"]:
            return str(row["project"]), "events_project"
        row2 = conn.execute(
            "SELECT project FROM events WHERE ts>=? "
            "AND source IN ('cursor','file','git','shell') "
            "AND project IS NOT NULL AND trim(project)!='' "
            "ORDER BY ts DESC LIMIT 1",
            (since,),
        ).fetchone()
        if row2 and row2["project"]:
            return str(row2["project"]), "events_recent"
    return None, "none"


def brief(project: str, *, prefer_detected: bool = False) -> dict[str, Any]:
    cfg = config.load_config()
    pid = workspace.normalize_project_id(project.strip()) if project.strip() else ""
    detected_from = "explicit"
    if not pid and prefer_detected:
        pid, detected_from = detect_active_project()
        if not pid:
            return {
                "project": "",
                "active": False,
                "detected_from": "none",
                "title": "",
                "purpose": "",
                "features": [],
                "completion": None,
                "completion_label": "未选择项目",
                "completion_source": "",
                "completion_complete": False,
                "completion_gates": {},
                "path": "",
                "lines": [
                    {"kind": "hint", "label": "当前", "text": "在问答 / 检索 / 项目页选择工作项目"},
                    {
                        "kind": "hint",
                        "label": "提示",
                        "text": "README「## 功能点」清单全 [x] + 稳定 14 天 + 认可 → 100%",
                    },
                ],
            }

    if not pid:
        return {"error": "缺少 project 参数", "project": ""}

    proj_dir = workspace.resolve_project_dir(pid)
    path_str = str(proj_dir.resolve()) if proj_dir else ""
    readme_text = ""
    parsed: dict[str, Any] = {
        "title": "",
        "purpose": "",
        "features": [],
        "feature_tasks": [],
        "opt_tasks": [],
    }
    if proj_dir:
        readme = proj_dir / "README.md"
        if readme.is_file():
            try:
                readme_text = readme.read_text(encoding="utf-8", errors="replace")
                parsed = parse_readme(readme_text)
            except OSError:
                pass

    display_name = parsed.get("title") or pid.split("/")[-1]
    purpose = parsed.get("purpose") or ""
    features = parsed.get("features") or []
    feature_tasks = parsed.get("feature_tasks") or []
    opt_tasks = parsed.get("opt_tasks") or []

    with db.session() as conn:
        last_ts = _last_activity_ts(conn, pid, proj_dir)
        docs = conn.execute(
            "SELECT COUNT(*) c FROM documents WHERE lower(project)=lower(?) OR lower(project)=lower(?)",
            (pid, f"cursor-{pid.split('/')[-1]}"),
        ).fetchone()["c"]

    comp = evaluate_completion(
        pid,
        readme_text,
        feature_tasks,
        opt_tasks,
        last_activity_ts=last_ts,
        dormant_days=int(cfg.get("completion_dormant_days", 14)),
        cfg=cfg,
    )
    completion = comp["pct"]
    completion_label = comp["label"]
    completion_hint = comp["hint"]
    completion_source = comp["source"]

    if not purpose and proj_dir:
        purpose = f"工作区项目 {pid}"
    if not features and docs:
        features = [f"已索引 {docs} 篇文档"]
    features_short = " · ".join(features[:3]) if features else "（README 未写功能点列表）"

    lines = [
        {"kind": "title", "label": "项目", "text": f"{pid} · {display_name}"},
        {"kind": "purpose", "label": "用途", "text": purpose or "（补充 README 首段或「用途」小节）"},
        {"kind": "features", "label": "功能", "text": features_short},
        {
            "kind": "completion",
            "label": "完成度",
            "text": completion_label,
            "pct": completion,
            "source": completion_hint,
        },
    ]
    if path_str:
        lines.append({"kind": "path", "label": "路径", "text": path_str})

    return {
        "project": pid,
        "active": True,
        "detected_from": detected_from,
        "title": display_name,
        "purpose": purpose,
        "features": features,
        "features_short": features_short,
        "feature_tasks": feature_tasks,
        "opt_tasks": opt_tasks,
        "completion": completion,
        "completion_label": completion_label,
        "completion_source": completion_source,
        "completion_hint": completion_hint,
        "completion_complete": comp["complete"],
        "completion_gates": comp["gates"],
        "path": path_str,
        "lines": lines,
    }
