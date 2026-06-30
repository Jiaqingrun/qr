"""主动提醒：项目休眠、规范偏差、RAG 质量下降。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import compliance, config, db, eval_suite, workspace


def _with_priority(alert: dict[str, Any]) -> dict[str, Any]:
    """P0 阻断 · P1 待办 · P2 信息。"""
    level = alert.get("level", "info")
    atype = alert.get("type", "")
    if level == "error" or atype == "backup":
        alert = {**alert, "priority": "p0"}
    elif level == "warn" or atype in ("standards", "rag"):
        alert = {**alert, "priority": "p1"}
    else:
        alert = {**alert, "priority": "p2"}
    return alert


def _projects_last_activity(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT project, MAX(ts) t FROM events WHERE project IS NOT NULL GROUP BY project"
    ).fetchall()
    return {r["project"]: int(r["t"]) for r in rows if r["project"]}


def check_dormant_projects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cfg = config.load_config()
    days = int(cfg.get("alert_dormant_days", 30))
    threshold = db.now() - days * 86400
    activity = _projects_last_activity(conn)
    alerts: list[dict[str, Any]] = []
    root = workspace.workspace_root(cfg)
    for cat in workspace.categories(cfg):
        cat_dir = root / cat
        if not cat_dir.is_dir():
            continue
        for proj in cat_dir.iterdir():
            if not proj.is_dir() or proj.name.startswith("."):
                continue
            pid = workspace.project_id(cat, proj.name)
            if workspace.is_protected_project(pid):
                continue
            last = activity.get(pid, 0)
            if last and last < threshold:
                idle = (db.now() - last) // 86400
                alerts.append(_with_priority({
                    "type": "dormant",
                    "level": "info",
                    "project": pid,
                    "message": f"项目 {pid} 已 {idle} 天无活动，可考虑归档",
                }))
            elif not last:
                alerts.append(_with_priority({
                    "type": "dormant",
                    "level": "info",
                    "project": pid,
                    "message": f"项目 {pid} 时间线无记录",
                }))
    return alerts


def check_standards_deviation(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cfg = config.load_config()
    ws = workspace.workspace_root(cfg)
    alerts: list[dict[str, Any]] = []
    scatter = config.expand_paths(cfg.get("scatter_roots", []))
    for base in scatter:
        if not base.exists():
            continue
        try:
            for child in base.iterdir():
                if not child.is_dir() or child.name.startswith("."):
                    continue
                if child.name in ("QR", "Library", "Applications"):
                    continue
                if workspace.is_under_workspace(child, cfg):
                    continue
                if (child / ".git").exists() or (child / "package.json").exists():
                    alerts.append(_with_priority({
                        "type": "standards",
                        "level": "warn",
                        "path": str(child),
                        "message": f"散落项目 {child.name} 不在 ~/QR，建议 qr workspace migrate",
                        "action": "settings_import",
                    }))
        except OSError:
            continue
    bad = [r for r in compliance.scan_index_roots() if not r["ok"]]
    for r in bad[:5]:
        name = Path(r["path"]).name
        alerts.append(_with_priority({
            "type": "compliance",
            "level": "info",
            "project": name,
            "message": f"合规待改进: {'; '.join(r['issues'][:2])}",
        }))
    return alerts


def check_rag_quality(conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    cfg = config.load_config()
    drop_pct = float(cfg.get("alert_rag_eval_drop_pct", 10))
    alerts: list[dict[str, Any]] = []
    own = conn is None
    if own:
        conn = db.connect()
    try:
        last = db.get_state(conn, "rag_eval_last_run")
        if last and db.now() - int(last) < 86400:
            return []
    finally:
        if own:
            conn.close()
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "model_eval", config.REPO_ROOT / "scripts" / "model_eval.py",
        )
        if spec is None or spec.loader is None:
            return alerts
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rows = mod.run_retrieval_baseline(include_extended=False)
        summary = eval_suite.summarize_rag(rows)
        rate = float(summary.get("retrieval_rate", 0))
        key = "rag_eval_rate"
        own = conn is None
        if own:
            conn = db.connect()
        try:
            prev_s = db.get_state(conn, key)
            db.set_state(conn, key, str(rate))
            db.set_state(conn, "rag_eval_last_run", str(db.now()))
            if own:
                conn.commit()
        finally:
            if own:
                conn.close()
        if prev_s:
            try:
                prev = float(prev_s)
                if prev - rate >= drop_pct:
                    alerts.append(_with_priority({
                        "type": "rag",
                        "level": "warn",
                        "message": f"RAG 命中率从 {prev:.0f}% 降至 {rate:.0f}%",
                        "action": "settings_quality",
                    }))
            except ValueError:
                pass
    except Exception:
        pass
    return alerts


def collect_all(conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    own = conn is None
    if own:
        db.init_db()
        conn = db.connect()
    try:
        out: list[dict[str, Any]] = []
        out.extend(check_dormant_projects(conn))
        out.extend(check_standards_deviation(conn))
        out.extend(check_rag_quality(conn))
        return out
    finally:
        if own:
            conn.close()


def persist_digest(alerts: list[dict[str, Any]]) -> Path:
    config.ensure_dirs()
    path = config.LOGS_DIR / "alerts-latest.json"
    path.write_text(json.dumps(alerts, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
