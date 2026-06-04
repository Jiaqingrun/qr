"""定时从 Cursor 对话摘要修订全局 / 项目规范（供 qr update / launchd 调用）。"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from . import config, db, governance, project_standards, standards_digest, workspace

_LOG = logging.getLogger("qr.standards_auto")
_STATE_KEY = "standards_auto_last_run"
_STATE_LOG = "standards_auto_last_result"


def _log_path() -> Path:
    config.ensure_dirs()
    return config.LOGS_DIR / "standards-auto.log"


def _setup_logging() -> None:
    path = _log_path()
    if _LOG.handlers:
        return
    _LOG.setLevel(logging.INFO)
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _LOG.addHandler(fh)


def _interval_seconds(cfg: dict[str, Any]) -> int:
    hours = float(cfg.get("standards_auto_interval_hours", 168))
    return max(3600, int(hours * 3600))


def should_run(cfg: dict[str, Any] | None = None, *, force: bool = False) -> bool:
    if force:
        return True
    cfg = cfg or config.load_config()
    if not cfg.get("standards_auto_revise", True):
        return False
    with db.session() as conn:
        last = db.get_state(conn, _STATE_KEY)
    if not last:
        return True
    try:
        elapsed = db.now() - int(last)
    except ValueError:
        return True
    return elapsed >= _interval_seconds(cfg)


def _active_workspace_projects(
    conn, start: int, end: int, *, limit: int
) -> list[str]:
    """近期有 Cursor 活动、且在工作区内的项目 ID。"""
    rows = conn.execute(
        "SELECT project, COUNT(*) c FROM events "
        "WHERE source='cursor' AND ts>=? AND ts<=? AND project IS NOT NULL "
        "GROUP BY project ORDER BY c DESC",
        (start, end),
    ).fetchall()
    ws_ids = {
        workspace.project_from_path(p)
        for p in governance.iter_workspace_projects()
    }
    out: list[str] = []
    for r in rows:
        ev_proj = (r["project"] or "").strip()
        if not ev_proj:
            continue
        for pid in ws_ids:
            if pid in out:
                continue
            keys = standards_digest.project_match_keys(pid)
            if ev_proj in keys or any(k in ev_proj for k in keys if len(k) > 2):
                if workspace.is_listable_project_id(pid):
                    out.append(pid)
                break
        if len(out) >= limit:
            break
    if not out and ws_ids:
        for pid in sorted(ws_ids):
            if workspace.is_listable_project_id(pid):
                out.append(pid)
            if len(out) >= min(limit, 1):
                break
    return out


def run_scheduled(
    period: str = "week",
    *,
    force: bool = False,
    global_only: bool = False,
    projects_only: bool = False,
) -> dict[str, Any]:
    """
    按配置自动修订全局规范与活跃项目 PROJECT.md。
    返回摘要供 update / 日志使用。
    """
    _setup_logging()
    cfg = config.load_config()
    result: dict[str, Any] = {
        "skipped": False,
        "global": None,
        "projects": [],
        "errors": [],
    }
    if not should_run(cfg, force=force):
        result["skipped"] = True
        result["reason"] = "未到间隔或 standards_auto_revise=false"
        _LOG.info("skip: %s", result["reason"])
        return result

    from . import summary

    start, end = summary._window(period)
    do_global = not projects_only and cfg.get("standards_auto_global", True)
    do_projects = not global_only and cfg.get("standards_auto_projects", True)
    max_proj = max(0, int(cfg.get("standards_auto_max_projects", 2)))

    from .ollama_client import OllamaError

    governance.ensure_standards()

    if do_global:
        try:
            _LOG.info("revise global period=%s from_conversations", period)
            content, saved = governance.revise_from_conversations(period)
            result["global"] = {
                "ok": True,
                "version_saved": saved,
                "chars": len(content),
            }
            _LOG.info("global ok saved=%s len=%s", saved, len(content))
        except (OllamaError, ValueError) as e:
            result["global"] = {"ok": False, "error": str(e)}
            result["errors"].append(f"global: {e}")
            _LOG.warning("global failed: %s", e)

    if do_projects and max_proj > 0:
        with db.session() as conn:
            targets = _active_workspace_projects(conn, start, end, limit=max_proj)
        for pid in targets:
            proj_dir = workspace.resolve_project_dir(pid)
            if not proj_dir:
                continue
            try:
                _LOG.info("revise project %s", pid)
                project_standards.ensure_project_standards(proj_dir, project_id=pid)
                content, saved = project_standards.revise_from_conversations(pid, period)
                result["projects"].append(
                    {
                        "project": pid,
                        "ok": True,
                        "version_saved": saved,
                        "chars": len(content),
                    }
                )
            except (OllamaError, ValueError) as e:
                result["projects"].append(
                    {"project": pid, "ok": False, "error": str(e)}
                )
                result["errors"].append(f"{pid}: {e}")
                _LOG.warning("project %s failed: %s", pid, e)

    if do_global and result.get("global", {}).get("ok"):
        try:
            governance.generate_rules_all_workspace()
            _LOG.info("regenerated rules for all workspace projects")
        except OSError as e:
            result["errors"].append(f"rules --all: {e}")

    with db.session() as conn:
        db.set_state(conn, _STATE_KEY, str(db.now()))
        db.set_state(conn, _STATE_LOG, json.dumps(result, ensure_ascii=False))

    _LOG.info("done: %s", json.dumps(result, ensure_ascii=False)[:500])
    return result
