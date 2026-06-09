"""全量自检与自动清理：无效索引、向量块、工作区幽灵项目、沿革噪声、稳定事实。"""
from __future__ import annotations

from typing import Any

from . import config, db, facts, governance, health, index_health, ui_audit, workspace


def prune_junk_projects() -> dict[str, Any]:
    """清理索引幽灵项目与 dev/qr-export 等无用条目（无需交互）。"""
    junk = workspace.list_junk_project_ids()
    pruned: list[str] = []
    errors: list[dict[str, str]] = []
    for pid in junk:
        strict = not workspace.parse_project_id(pid)[0] or (
            workspace._resolve_project_dir_exact(pid) is None
        )
        try:
            workspace.purge_project(
                pid,
                confirm=pid,
                confirm_phrase=workspace._DELETE_CONFIRM_PHRASE,
                strict_id=strict,
            )
            pruned.append(pid)
        except ValueError as e:
            errors.append({"project": pid, "error": str(e)})
    return {"junk": junk, "pruned": pruned, "errors": errors}


def run_full_maintenance(*, fix: bool = True) -> dict[str, Any]:
    """全量自检；fix=True 时执行清理（无效指向、孤儿向量、幽灵项目等）。"""
    db.init_db()
    report: dict[str, Any] = {"fix": fix, "steps": {}}

    with db.session() as conn:
        report["index_before"] = index_health.scan(conn)
        vec_before = index_health.scan_vectors(conn)
        report["vectors_before"] = vec_before

        if fix:
            report["steps"]["orphan_docs"] = index_health.cleanup_orphans(conn)
            report["steps"]["stale_vec"] = index_health.cleanup_stale_vectors(conn)
            report["steps"]["wrong_dim"] = index_health.cleanup_wrong_dim_chunks(conn)
            synced = db.sync_vec(conn)
            conn.commit()
            report["steps"]["vec_synced"] = {"added": synced}
            report["index_after"] = index_health.scan(conn)
            report["vectors_after"] = index_health.scan_vectors(conn)
            try:
                report["steps"]["wal_checkpoint"] = db.checkpoint_wal(truncate=True)
            except Exception as exc:
                report["steps"]["wal_checkpoint"] = {"error": str(exc)}

    if fix:
        health.invalidate_status_cache()
        report["steps"]["workspace_prune"] = prune_junk_projects()
        report["steps"]["governance"] = {
            "noise_removed": governance.prune_noise_versions(),
            "redundant": governance.prune_redundant_versions(),
        }
        report["steps"]["facts"] = {"updated": len(facts.sync_from_config())}

    report["ui_audit"] = ui_audit.audit_ui(strict_api=True)
    report["doctor"] = health.diagnose()
    report["ok"] = report["doctor"].get("ok", False) and report["ui_audit"].get("ok", False)
    return report
