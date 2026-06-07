"""数据库备份：恢复、校验、轮转。"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, db


def _backups_dir() -> Path:
    d = config.QR_HOME / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_backup_files() -> list[Path]:
    d = _backups_dir()
    if not d.exists():
        return []
    return sorted(d.glob("qr-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)


def verify_backup(path: Path | str) -> dict[str, Any]:
    """检查备份文件是否可读且为有效 SQLite。"""
    p = Path(path).expanduser().resolve()
    out: dict[str, Any] = {"path": str(p), "ok": False}
    if not p.is_file():
        out["error"] = "文件不存在"
        return out
    st = p.stat()
    out["size_mb"] = round(st.st_size / (1024 * 1024), 2)
    out["mtime"] = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
    if st.st_size < 4096:
        out["error"] = "文件过小，可能损坏"
        return out
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {"events", "documents", "chunks", "state"}
        missing = sorted(required - tables)
        if missing:
            out["error"] = f"缺少表: {', '.join(missing)}"
            conn.close()
            return out
        out["events"] = int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        out["documents"] = int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        conn.close()
        out["ok"] = True
        return out
    except sqlite3.Error as e:
        out["error"] = str(e)
        return out


def prune_backups(keep: int | None = None) -> dict[str, Any]:
    cfg = config.load_config()
    keep_n = int(keep if keep is not None else cfg.get("backup_keep_count", 10))
    keep_n = max(1, keep_n)
    files = list_backup_files()
    removed: list[str] = []
    for p in files[keep_n:]:
        try:
            p.unlink()
            removed.append(p.name)
        except OSError:
            pass
    return {"kept": min(len(files), keep_n), "removed": removed}


def run_backup(dest: str = "") -> dict[str, str]:
    config.ensure_dirs()
    if dest:
        out = Path(dest).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = _backups_dir() / f"qr-{stamp}.db"
    shutil.copy2(config.DB_PATH, out)
    prune_backups()
    return {"path": str(out), "name": out.name}


def restore_backup(
    src: Path | str,
    *,
    safety_copy: bool = True,
) -> dict[str, Any]:
    """从备份恢复 qr.db；恢复前可选保存当前库为 qr-pre-restore-*.db。"""
    p = Path(src).expanduser().resolve()
    check = verify_backup(p)
    if not check.get("ok"):
        return {"ok": False, "error": check.get("error", "备份无效"), "verify": check}

    config.ensure_dirs()
    safety_path = ""
    if safety_copy and config.DB_PATH.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safety = _backups_dir() / f"qr-pre-restore-{stamp}.db"
        shutil.copy2(config.DB_PATH, safety)
        safety_path = str(safety)

    shutil.copy2(p, config.DB_PATH)
    db.init_db()
    return {
        "ok": True,
        "restored_from": str(p),
        "safety_copy": safety_path,
        "verify": check,
    }
