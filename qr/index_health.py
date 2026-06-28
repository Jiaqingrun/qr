"""向量索引健康：孤儿文档、失效路径检测与清理。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from . import config, db


def _is_missing_path(path_s: str) -> bool:
    if not path_s:
        return True
    if path_s.startswith("cursor_chats/"):
        p = config.QR_HOME / path_s
        return not p.is_file()
    p = Path(path_s).expanduser()
    try:
        return not p.is_file()
    except OSError:
        return True


def scan(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    own = conn is None
    if own:
        db.init_db()
        conn = db.connect()
    try:
        rows = conn.execute("SELECT id, path, project FROM documents").fetchall()
        missing_file: list[dict] = []
        stale_cursor: list[dict] = []
        for r in rows:
            path_s = r["path"] or ""
            if _is_missing_path(path_s):
                item = {"id": r["id"], "path": path_s, "project": r["project"]}
                missing_file.append(item)
                if "/agent-transcripts/" in path_s or path_s.endswith(".jsonl"):
                    stale_cursor.append(item)
        backup_issues: list[str] = []
        backup_level = "info"
        from . import backup_ops

        backups = backup_ops.list_backup_files()[:3]
        latest = backup_ops.latest_backup_info()
        cfg = config.load_config()
        warn_days = max(1, int(cfg.get("backup_warn_days", 7)))
        if not backups:
            backup_issues.append("尚无数据库备份")
            backup_level = "warn"
        else:
            bad = [b.name for b in backups if not backup_ops.verify_backup(b).get("ok")]
            if bad:
                backup_issues.append(f"备份损坏: {', '.join(bad)}")
                backup_level = "warn"
            elif latest.get("age_days") is not None and latest["age_days"] > warn_days:
                backup_issues.append(
                    f"上次备份 {latest['age_days']:.0f} 天前（超过 {warn_days} 天）"
                )
                backup_level = "warn"
        return {
            "documents": len(rows),
            "missing_files": len(missing_file),
            "stale_cursor": len(stale_cursor),
            "missing_samples": missing_file[:8],
            "stale_cursor_samples": stale_cursor[:8],
            "backup_issues": backup_issues,
            "backup_level": backup_level,
            "latest_backup": latest,
            "ok": not missing_file and not backup_issues,
        }
    finally:
        if own:
            conn.close()


def cleanup_orphans(
    conn: sqlite3.Connection | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    own = conn is None
    if own:
        db.init_db()
        conn = db.connect()
    stats = {"documents_removed": 0, "chunks_removed": 0}
    try:
        rep = scan(conn)
        ids = [x["id"] for x in rep.get("missing_samples", [])]
        all_missing = conn.execute("SELECT id, path FROM documents").fetchall()
        ids = [int(r["id"]) for r in all_missing if _is_missing_path(r["path"] or "")]
        if dry_run:
            for did in ids:
                n = conn.execute(
                    "SELECT COUNT(*) c FROM chunks WHERE doc_id=?", (did,),
                ).fetchone()["c"]
                stats["documents_removed"] += 1
                stats["chunks_removed"] += int(n)
            return stats
        for did in ids:
            if db.vec_available():
                cids = [
                    r["id"]
                    for r in conn.execute(
                        "SELECT id FROM chunks WHERE doc_id=?", (did,),
                    ).fetchall()
                ]
                for cid in cids:
                    conn.execute("DELETE FROM vec_chunks WHERE rowid=?", (cid,))
            db.fts_delete_doc(conn, did)
            n = conn.execute(
                "SELECT COUNT(*) c FROM chunks WHERE doc_id=?", (did,),
            ).fetchone()["c"]
            conn.execute("DELETE FROM chunks WHERE doc_id=?", (did,))
            conn.execute("DELETE FROM documents WHERE id=?", (did,))
            stats["documents_removed"] += 1
            stats["chunks_removed"] += int(n)
        if stats["documents_removed"]:
            conn.commit()
        return stats
    finally:
        if own:
            conn.close()


def scan_vectors(conn: sqlite3.Connection) -> dict[str, Any]:
    """检测孤儿向量块、维度不一致块、vec 表与 chunks 不同步。"""
    expected_dim = int(config.load_config().get("embed_dim", 768))
    wrong_dim = int(
        conn.execute(
            "SELECT COUNT(*) c FROM chunks WHERE dim != ?", (expected_dim,),
        ).fetchone()["c"]
    )
    stale_vec = 0
    missing_vec = 0
    if db.vec_available():
        stale_vec = int(
            conn.execute(
                "SELECT COUNT(*) c FROM vec_chunks "
                "WHERE rowid NOT IN (SELECT id FROM chunks)",
            ).fetchone()["c"]
        )
        missing_vec = int(
            conn.execute(
                "SELECT COUNT(*) c FROM chunks "
                "WHERE id NOT IN (SELECT rowid FROM vec_chunks)",
            ).fetchone()["c"]
        )
    return {
        "embed_dim": expected_dim,
        "wrong_dim_chunks": wrong_dim,
        "stale_vec_rows": stale_vec,
        "missing_vec_rows": missing_vec,
        "ok": wrong_dim == 0 and stale_vec == 0 and missing_vec == 0,
    }


def _delete_document(conn: sqlite3.Connection, doc_id: int) -> int:
    cids = [
        int(r["id"])
        for r in conn.execute("SELECT id FROM chunks WHERE doc_id=?", (doc_id,)).fetchall()
    ]
    for cid in cids:
        if db.vec_available():
            try:
                conn.execute("DELETE FROM vec_chunks WHERE rowid=?", (cid,))
            except sqlite3.OperationalError:
                pass
    db.fts_delete_doc(conn, doc_id)
    conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    return len(cids)


def cleanup_stale_vectors(
    conn: sqlite3.Connection | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """删除 vec_chunks 中无对应 chunk 的孤儿行。"""
    own = conn is None
    if own:
        db.init_db()
        conn = db.connect()
    stats = {"vec_removed": 0}
    try:
        if not db.vec_available():
            return stats
        rows = conn.execute(
            "SELECT rowid FROM vec_chunks "
            "WHERE rowid NOT IN (SELECT id FROM chunks)",
        ).fetchall()
        stats["vec_removed"] = len(rows)
        if dry_run or not rows:
            return stats
        for r in rows:
            conn.execute("DELETE FROM vec_chunks WHERE rowid=?", (int(r["rowid"]),))
        conn.commit()
        return stats
    finally:
        if own:
            conn.close()


def cleanup_wrong_dim_chunks(
    conn: sqlite3.Connection | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """删除维度与 embed_dim 不一致的文档索引（需后续 qr index 补回）。"""
    own = conn is None
    if own:
        db.init_db()
        conn = db.connect()
    expected_dim = int(config.load_config().get("embed_dim", 768))
    stats = {"documents_removed": 0, "chunks_removed": 0}
    try:
        doc_ids = [
            int(r["doc_id"])
            for r in conn.execute(
                "SELECT DISTINCT doc_id FROM chunks WHERE dim != ?", (expected_dim,),
            ).fetchall()
        ]
        if dry_run:
            for did in doc_ids:
                n = conn.execute(
                    "SELECT COUNT(*) c FROM chunks WHERE doc_id=?", (did,),
                ).fetchone()["c"]
                stats["documents_removed"] += 1
                stats["chunks_removed"] += int(n)
            return stats
        for did in doc_ids:
            stats["chunks_removed"] += _delete_document(conn, did)
            stats["documents_removed"] += 1
        if stats["documents_removed"]:
            conn.commit()
        return stats
    finally:
        if own:
            conn.close()


def maybe_auto_cleanup(
    conn: sqlite3.Connection,
    *,
    force: bool = False,
) -> dict[str, Any] | None:
    """M4-3：按配置周期自动清理源文件已消失的孤儿索引。"""
    cfg = config.load_config()
    if not force and not cfg.get("index_health_auto", True):
        return None
    interval = max(1, int(cfg.get("index_health_auto_days", 7))) * 86400
    now = db.now()
    if not force:
        last = int(db.get_state(conn, "index_health_auto_last") or "0")
        if now - last < interval:
            return None
    before = scan(conn)
    missing = int(before.get("missing_files") or 0)
    if missing <= 0:
        db.set_state(conn, "index_health_auto_last", str(now))
        return {"ran": False, "reason": "no_orphans", "missing_files": 0}
    stats = cleanup_orphans(conn, dry_run=False)
    db.set_state(conn, "index_health_auto_last", str(now))
    samples = [
        s.get("path", "") for s in (before.get("missing_samples") or [])[:5]
    ]
    return {
        "ran": True,
        "missing_before": missing,
        "cleanup": stats,
        "sample_paths": samples,
    }
