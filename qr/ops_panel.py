"""Web 运维面板：自检、备份、定时任务、导入发现、Git 扫描目录对齐。"""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, db, health, importer, permissions

_AGENT_LABELS = [
    "com.qr.tracker",
    "com.qr.cursor",
    "com.qr.auto",
    "com.qr.weekly",
    "com.qr.daily",
    "com.qr.web",
    "com.qr.web-watch",
]

_AGENT_TITLES = {
    "com.qr.tracker": "应用追踪",
    "com.qr.cursor": "Cursor 同步",
    "com.qr.auto": "自动收录",
    "com.qr.weekly": "每周总结",
    "com.qr.daily": "每日总结",
    "com.qr.web": "Web 服务",
    "com.qr.web-watch": "Web 健康巡检",
}


def schedule_detail() -> dict[str, Any]:
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    try:
        out = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    agents = []
    loaded_n = 0
    for label in _AGENT_LABELS:
        path = plist_dir / f"{label}.plist"
        loaded = label in out
        installed = path.exists()
        if loaded:
            loaded_n += 1
        agents.append(
            {
                "label": label,
                "title": _AGENT_TITLES.get(label, label),
                "installed": installed,
                "loaded": loaded,
            }
        )
    return {
        "agents": agents,
        "loaded": loaded_n,
        "total": len(_AGENT_LABELS),
        "all_loaded": loaded_n == len(_AGENT_LABELS),
    }


def list_backups(limit: int = 8) -> list[dict[str, Any]]:
    from . import backup_ops

    rows: list[dict[str, Any]] = []
    for p in backup_ops.list_backup_files():
        st = p.stat()
        verify = backup_ops.verify_backup(p)
        rows.append(
            {
                "path": str(p),
                "name": p.name,
                "size_mb": round(st.st_size / (1024 * 1024), 2),
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "ok": verify.get("ok", False),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def run_backup(dest: str = "") -> dict[str, str]:
    from . import backup_ops

    return backup_ops.run_backup(dest)


def restore_backup(path: str) -> dict[str, Any]:
    from . import backup_ops

    return backup_ops.restore_backup(path)


def index_health(*, cleanup: bool = False) -> dict[str, Any]:
    from . import index_health as ih

    with db.session() as conn:
        rep = ih.scan(conn)
        if cleanup:
            rep["cleanup"] = ih.cleanup_orphans(conn, dry_run=False)
    return rep


def sync_git_scan_roots() -> dict[str, Any]:
    cfg = config.load_config()
    before = list(cfg.get("git_scan_roots") or [])
    index = list(cfg.get("index_roots") or [])
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*before, *index]:
        s = str(item)
        if s in seen:
            continue
        seen.add(s)
        merged.append(s)
    added = [p for p in merged if p not in before]
    if added:
        cfg["git_scan_roots"] = merged
        config.save_config(cfg)
    return {"before": before, "after": merged, "added": added, "changed": bool(added)}


def discover_imports() -> list[dict[str, str]]:
    return [{"path": str(p), "name": p.name} for p in importer.discover()]


def import_paths(paths: list[str], move: bool = False) -> dict[str, Any]:
    selected: list[Path] = []
    for s in paths:
        p = Path(s).expanduser()
        if p.exists():
            selected.append(p)
    if not selected:
        return {"ok": False, "error": "未选择有效路径", "added": [], "moved": []}
    if move:
        moved = importer.move_to_projects(selected)
        return {"ok": True, "added": [], "moved": [{"src": a, "dest": b} for a, b in moved]}
    added = importer.add_to_index(selected)
    return {"ok": True, "added": added, "moved": []}


def overview(conn=None) -> dict[str, Any]:
    own = conn is None
    if own:
        db.init_db()
        conn = db.connect()
    try:
        rep = health.diagnose(conn)
        cfg = config.load_config()
        git_n = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE source='git'"
        ).fetchone()["c"]
        index_roots = [str(p) for p in config.expand_paths(cfg.get("index_roots", []))]
        git_roots = [str(p) for p in config.git_roots(cfg)]
        missing_git = sorted(set(index_roots) - set(git_roots))
        return {
            "doctor": rep,
            "schedule": schedule_detail(),
            "backups": list_backups(),
            "permissions": permissions.probe_access(),
            "trusted": permissions.trusted_executables(),
            "config": {
                "path": str(config.CONFIG_PATH),
                "index_roots": cfg.get("index_roots", []),
                "git_scan_roots": cfg.get("git_scan_roots", []),
                "git_scan_roots_expanded": git_roots,
                "git_events": git_n,
                "git_roots_missing_from_index": missing_git,
            },
            "qr_home": str(config.QR_HOME),
            "db_path": str(config.DB_PATH),
        }
    finally:
        if own:
            conn.close()


def install_schedule(*, include_web: bool = False) -> dict[str, Any]:
    from . import schedule_service

    try:
        result = schedule_service.install_all(include_web=include_web)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": True,
        **result,
        "schedule": schedule_detail(),
    }
