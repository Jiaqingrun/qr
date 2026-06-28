from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

from . import config, db

_REQUIRED_MARKERS = ("README.md",)
_OPTIONAL_MARKERS = ("AGENTS.md", ".cursor/rules", "pyproject.toml", "package.json")


def check_project(path: Path) -> dict:
    path = path.expanduser().resolve()
    issues: list[str] = []
    ok_items: list[str] = []
    if not path.is_dir():
        return {"path": str(path), "ok": False, "issues": ["目录不存在"], "ok_items": []}
    names = {p.name for p in path.iterdir()} if path.exists() else set()
    for m in _REQUIRED_MARKERS:
        if m in names or (path / m).exists():
            ok_items.append(m)
        else:
            issues.append(f"缺少 {m}")
    rules = path / ".cursor" / "rules"
    if rules.is_dir() and any(rules.glob("*.mdc")):
        ok_items.append(".cursor/rules")
    elif (path / "AGENTS.md").exists():
        ok_items.append("AGENTS.md")
    else:
        issues.append("缺少 AGENTS.md 或 .cursor/rules")
    from . import project_standards, workspace

    if workspace.is_under_workspace(path):
        proj_body = project_standards.read_project_standards(path)
        if proj_body:
            ok_items.append("PROJECT.md")
            mixed = project_standards.mixed_standards_issues(proj_body)
            if mixed:
                issues.append(
                    "PROJECT.md 混入全局规范（应分层、不混写）：" + "；".join(mixed[:3])
                )
        else:
            issues.append("缺少 PROJECT.md（项目级规范，可用 qr project standards --edit 创建）")
        personal = rules / "00-personal-standards.mdc"
        if not personal.is_file():
            issues.append("缺少 00-personal-standards.mdc（请 qr rules 生成）")
    standards = config.STANDARDS_PATH
    if standards.exists():
        ok_items.append("个人规范已配置")
    else:
        issues.append("QR本地知识库数据目录 ~/.qr/standards.md 未初始化")
    return {"path": str(path), "ok": len(issues) == 0, "issues": issues, "ok_items": ok_items}


def scan_index_roots() -> list[dict]:
    from . import workspace

    cfg = config.load_config()
    roots = config.expand_paths(cfg.get("index_roots", []))
    results = []
    ws = workspace.workspace_root(cfg)
    cats = set(workspace.categories(cfg))
    for root in roots:
        if not root.exists():
            continue
        try:
            for child in root.iterdir():
                if not child.is_dir() or child.name.startswith("."):
                    continue
                if root.resolve() == ws.resolve() and child.name in cats:
                    for proj in child.iterdir():
                        if proj.is_dir() and not proj.name.startswith("."):
                            results.append(check_project(proj))
                    continue
                marker_exists = any((child / m).exists() for m in _OPTIONAL_MARKERS)
                if not marker_exists and not (child / ".git").exists():
                    continue
                results.append(check_project(child))
        except OSError:
            continue
    return results


def knowledge_graph(limit: int = 40) -> dict:
    with db.session() as conn:
        rows = conn.execute(
            "SELECT project, source, title FROM events "
            "WHERE project IS NOT NULL AND project != '' "
            "ORDER BY ts DESC LIMIT 5000"
        ).fetchall()
        docs = conn.execute(
            "SELECT DISTINCT project, ext FROM documents WHERE project IS NOT NULL"
        ).fetchall()
    nodes: dict[str, dict] = {}
    edges: Counter = Counter()

    def add_node(name: str, kind: str, **extra):
        n = nodes.setdefault(name, {"id": name, "kind": kind, "count": 0, **extra})
        n["count"] += 1

    for r in rows:
        proj = r["project"] or "unknown"
        add_node(proj, "project")
        src = r["source"]
        add_node(src, "source")
        edges[(proj, src)] += 1
        title = (r["title"] or "")[:40]
        if title and src == "cursor":
            add_node(title, "topic")

    for r in docs:
        proj = r["project"] or "unknown"
        ext = (r["ext"] or "").lstrip(".")
        if ext:
            tech = ext
            add_node(tech, "tech")
            edges[(proj, tech)] += 1

    edge_list = [
        {"from": a, "to": b, "weight": w}
        for (a, b), w in edges.most_common(limit)
    ]
    node_list = sorted(nodes.values(), key=lambda x: -x["count"])[:limit]
    return {"nodes": node_list, "edges": edge_list}


def _ship_days(cfg: dict | None, days: int | None) -> int:
    cfg = cfg or config.load_config()
    if days and days > 0:
        return int(days)
    return max(1, int(cfg.get("compliance_ship_days", 14)))


def _project_active(conn: sqlite3.Connection, pid: str, since: int) -> bool:
    from . import workspace

    pvals = workspace.project_filter_values(pid)
    if not pvals:
        pvals = [pid]
    ph = ",".join("?" * len(pvals))
    row = conn.execute(
        f"SELECT 1 FROM events WHERE ts>=? AND project IN ({ph}) LIMIT 1",
        (since, *pvals),
    ).fetchone()
    return row is not None


def _decision_count(conn: sqlite3.Connection, pid: str, since: int) -> int:
    from . import workspace

    pvals = workspace.project_filter_values(pid)
    if not pvals:
        pvals = [pid]
    ph = ",".join("?" * len(pvals))
    row = conn.execute(
        f"SELECT COUNT(*) c FROM events WHERE source='note' AND ts>=? "
        f"AND project IN ({ph}) AND content LIKE '%决策记录%'",
        (since, *pvals),
    ).fetchone()
    return int(row["c"] if row else 0)


def _has_ship_check(conn: sqlite3.Connection, pid: str, since: int, cfg: dict) -> bool:
    from . import workspace

    at = db.get_state(conn, f"ship_check_at:{pid}")
    if at:
        try:
            if int(at) >= since:
                return True
        except ValueError:
            pass
    last_at = db.get_state(conn, "ship_check_last_at")
    last_proj = db.get_state(conn, "ship_check_last_project") or ""
    canon_last = workspace.canonical_project_id(last_proj, cfg) or last_proj
    if last_at and canon_last == pid:
        try:
            if int(last_at) >= since:
                return True
        except ValueError:
            pass
    slug = pid.split("/")[-1]
    row = conn.execute(
        "SELECT 1 FROM events WHERE ts>=? AND source='qr' AND ("
        "json_extract(meta,'$.action')='cli:ship-check' OR title LIKE '%设计者验收%'"
        ") AND (project=? OR content LIKE ? OR content LIKE ?) LIMIT 1",
        (since, pid, f"%{pid}%", f"%{slug}%"),
    ).fetchone()
    return row is not None


def _has_doctor_run(conn: sqlite3.Connection, since: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM events WHERE ts>=? AND source='qr' AND ("
        "json_extract(meta,'$.action')='cli:doctor' OR title LIKE '%系统自检%'"
        ") LIMIT 1",
        (since,),
    ).fetchone()
    return row is not None


def scan_ship_checks(
    conn: sqlite3.Connection | None = None,
    *,
    days: int | None = None,
    cfg: dict | None = None,
) -> dict:
    """设计者验收清单：近 N 天活跃项目的决策与 ship-check/doctor 记录。"""
    from . import workspace

    cfg = cfg or config.load_config()
    span = _ship_days(cfg, days)
    since = db.now() - span * 86400
    own = conn is None
    if own:
        db.init_db()
        conn = db.connect()
    try:
        projects: list[dict] = []
        missing_decisions: list[str] = []
        missing_ship: list[str] = []
        doctor_recent = _has_doctor_run(conn, since)
        root = workspace.workspace_root(cfg)
        for cat in workspace.categories(cfg):
            for pid, proj_dir in workspace.iter_category_project_dirs(root, cat):
                if not workspace.is_listable_project_id(pid, cfg):
                    continue
                active = _project_active(conn, pid, since)
                decisions = _decision_count(conn, pid, since)
                ship_ok = _has_ship_check(conn, pid, since, cfg)
                warnings: list[str] = []
                if active:
                    if decisions <= 0:
                        warnings.append(f"近 {span} 天无决策记录（qr log --type decision）")
                        missing_decisions.append(pid)
                    if not ship_ok and not doctor_recent:
                        warnings.append(
                            f"近 {span} 天无设计者验收（qr ship-check -p {pid} 或 qr doctor）"
                        )
                        missing_ship.append(pid)
                item = {
                    "project": pid,
                    "path": str(proj_dir),
                    "active": active,
                    "decisions": decisions,
                    "ship_check": ship_ok,
                    "doctor_recent": doctor_recent,
                    "ok": not warnings,
                    "warnings": warnings,
                }
                projects.append(item)
        projects.sort(key=lambda x: (x["ok"], not x["active"], x["project"]))
        return {
            "days": span,
            "doctor_recent": doctor_recent,
            "projects": projects,
            "missing_decisions": missing_decisions,
            "missing_ship": missing_ship,
            "ok": not missing_decisions and not missing_ship,
        }
    finally:
        if own:
            conn.close()
