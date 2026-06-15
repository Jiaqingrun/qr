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
