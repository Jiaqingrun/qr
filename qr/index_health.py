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
        from . import backup_ops

        backups = backup_ops.list_backup_files()[:3]
        if not backups:
            backup_issues.append("尚无数据库备份")
        else:
            bad = [b.name for b in backups if not backup_ops.verify_backup(b).get("ok")]
            if bad:
                backup_issues.append(f"备份损坏: {', '.join(bad)}")
        return {
            "documents": len(rows),
            "missing_files": len(missing_file),
            "stale_cursor": len(stale_cursor),
            "missing_samples": missing_file[:8],
            "stale_cursor_samples": stale_cursor[:8],
            "backup_issues": backup_issues,
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
