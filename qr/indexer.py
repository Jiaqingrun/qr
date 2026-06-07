from __future__ import annotations

import fnmatch
import hashlib
import os
import sqlite3
from pathlib import Path

from . import config, db, scan_paths
from .ollama_client import Ollama
from .vectors import to_blob

META_FILES: list[tuple[Path, str]] = [
    (config.CONFIG_PATH, "qr-config"),
    (config.STANDARDS_PATH, "qr-standards"),
]


def _meta_text(path: Path, raw: str) -> str:
    if path.resolve() != config.CONFIG_PATH.resolve():
        return raw
    cfg = config.load_config()
    host = cfg.get("web_host", "127.0.0.1")
    port = cfg.get("web_port", 8765)
    return "\n".join([
        "# QR本地知识库运行时配置 (config.json)",
        f"路径: {path}",
        f"Web 服务默认监听: {host}:{port}",
        f"向量嵌入模型 embed_model: {cfg.get('embed_model')}",
        f"日常问答模型 chat_model: {cfg.get('chat_model')}",
        f"深度推理模型 deep_model: {cfg.get('deep_model')}",
        f"context_tokens: {cfg.get('context_tokens')}",
        f"deep_context_tokens: {cfg.get('deep_context_tokens')}",
        "",
        "## 原始 config.json",
        raw,
    ])


def _index_document(
    conn,
    p: Path,
    project: str,
    raw: str,
    cfg: dict,
    ol: Ollama,
    reindex: bool,
    stats: dict[str, int],
    progress=None,
) -> None:
    raw = _meta_text(p, raw)
    h = hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()
    path_s = str(p.resolve())
    row = conn.execute("SELECT id, hash FROM documents WHERE path=?", (path_s,)).fetchone()
    if row and row["hash"] == h and not reindex:
        stats["skipped"] += 1
        return
    from . import chunking

    chunks = chunking.chunk_document(p, raw, cfg)
    if not chunks:
        return
    if row:
        db.fts_delete_doc(conn, row["id"])
        if db.vec_available():
            old_ids = [r["id"] for r in conn.execute(
                "SELECT id FROM chunks WHERE doc_id=?", (row["id"],)).fetchall()]
            for cid in old_ids:
                conn.execute("DELETE FROM vec_chunks WHERE rowid=?", (cid,))
        conn.execute("DELETE FROM chunks WHERE doc_id=?", (row["id"],))
        doc_id = row["id"]
        conn.execute(
            "UPDATE documents SET project=?,ext=?,mtime=?,hash=?,n_chunks=?,indexed_at=? WHERE id=?",
            (project, p.suffix.lower(), p.stat().st_mtime, h, len(chunks), db.now(), doc_id),
        )
    else:
        cur = conn.execute(
            "INSERT INTO documents(path,project,ext,mtime,hash,n_chunks,indexed_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (path_s, project, p.suffix.lower(), p.stat().st_mtime, h, len(chunks), db.now()),
        )
        doc_id = cur.lastrowid
    for i, ch in enumerate(chunks):
        emb = ol.embed(ch)
        blob = to_blob(emb)
        cur = conn.execute(
            "INSERT INTO chunks(doc_id,ordinal,text,dim,embedding) VALUES(?,?,?,?,?)",
            (doc_id, i, ch, len(emb), blob),
        )
        if db.vec_available():
            conn.execute("INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                         (cur.lastrowid, blob))
        db.fts_index_chunk(conn, int(cur.lastrowid), path_s, project, ch)
        stats["chunks"] += 1
    from . import symbol_index

    if p.suffix.lower() in symbol_index._SYMBOL_EXTS:
        symbol_index.sync_path(conn, p, project, raw)
    stats["files"] += 1
    if progress:
        progress(path_s, len(chunks))
    conn.commit()


def path_excluded(p: Path, patterns: list[str]) -> bool:
    if not patterns:
        return False
    name = p.name
    full = str(p.resolve()).replace("\\", "/")
    for pat in patterns:
        if not pat:
            continue
        if "/" in pat or "**" in pat:
            if fnmatch.fnmatch(full, pat) or fnmatch.fnmatch(full, f"*/{pat.lstrip('/')}"):
                return True
            continue
        if fnmatch.fnmatch(name, pat) or pat in full:
            return True
    return False


def purge_excluded_documents(patterns: list[str] | None = None) -> dict[str, int]:
    """从索引中删除命中 exclude 规则的文件（如评测脚本）。"""
    cfg = config.load_config()
    patterns = patterns if patterns is not None else list(
        cfg.get("index_exclude_path_patterns") or [],
    )
    if not patterns:
        return {"documents_removed": 0}
    removed = 0
    with db.session() as conn:
        rows = conn.execute("SELECT id, path FROM documents").fetchall()
        for row in rows:
            try:
                p = Path(row["path"])
            except (TypeError, ValueError):
                continue
            if not path_excluded(p, patterns):
                continue
            doc_id = int(row["id"])
            if db.vec_available():
                chunk_rows = conn.execute(
                    "SELECT id FROM chunks WHERE doc_id=?", (doc_id,),
                ).fetchall()
                for cr in chunk_rows:
                    try:
                        conn.execute(
                            "DELETE FROM vec_chunks WHERE rowid=?", (int(cr["id"]),),
                        )
                    except sqlite3.OperationalError:
                        pass
            db.fts_delete_doc(conn, doc_id)
            conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            from . import symbol_index

            symbol_index.remove_path(conn, str(p.resolve()))
            removed += 1
        conn.commit()
    return {"documents_removed": removed}


def _iter_files(roots, exclude, exts, max_bytes, path_patterns, *, min_mtime: float | None = None):
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            scan_paths.prune_walk_dirnames(dirnames, Path(dirpath))
            dirnames[:] = [d for d in dirnames if d not in exclude
                           and not d.startswith(".") and not d.endswith(".egg-info")]
            for fn in filenames:
                p = Path(dirpath) / fn
                if path_excluded(p, path_patterns):
                    continue
                if p.suffix.lower() not in exts:
                    continue
                try:
                    st = p.stat()
                    if st.st_size > max_bytes:
                        continue
                    if min_mtime is not None and st.st_mtime < min_mtime:
                        continue
                except OSError:
                    continue
                yield root, p


def _since_ts_from_cfg(cfg: dict, since_days: float | None, since_hours: float | None) -> int | None:
    if since_hours is not None and since_hours > 0:
        return db.now() - int(since_hours * 3600)
    if since_days is not None and since_days > 0:
        return db.now() - int(since_days * 86400)
    if cfg.get("index_incremental_after_ingest", True):
        with db.session() as conn:
            raw = db.get_state(conn, "ingest_last_ts") or db.get_state(conn, "index_last_ts")
        if raw:
            try:
                return int(raw)
            except ValueError:
                pass
    return None


def index_meta(reindex: bool = False, progress=None) -> dict[str, int]:
    cfg = config.load_config()
    ol = Ollama()
    stats = {"files": 0, "chunks": 0, "skipped": 0}
    with db.session() as conn:
        for path, project in META_FILES:
            if not path.exists():
                continue
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            _index_document(conn, path, project, raw, cfg, ol, reindex, stats, progress)
    return stats


def index(
    reindex: bool = False,
    progress=None,
    *,
    since_days: float | None = None,
    since_hours: float | None = None,
    incremental: bool = False,
) -> dict[str, int]:
    cfg = config.load_config()
    purge = purge_excluded_documents(list(cfg.get("index_exclude_path_patterns") or []))
    roots = config.expand_paths(cfg["index_roots"])
    exclude = set(cfg["index_exclude_dirs"])
    path_patterns = list(cfg.get("index_exclude_path_patterns") or [])
    exts = set(cfg["index_extensions"])
    max_bytes = int(cfg["max_file_bytes"])
    ol = Ollama()

    min_mtime: float | None = None
    since_ts: int | None = None
    if not reindex and (incremental or since_days or since_hours):
        since_ts = _since_ts_from_cfg(cfg, since_days, since_hours)
        if since_ts:
            min_mtime = float(since_ts)

    stats = {"files": 0, "chunks": 0, "skipped": 0, "incremental": int(min_mtime is not None), **purge}
    with db.session() as conn:
        for root, p in _iter_files(
            roots, exclude, exts, max_bytes, path_patterns, min_mtime=min_mtime,
        ):
            try:
                raw = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            from . import workspace

            project = workspace.project_from_path(p, root)
            _index_document(conn, p, project, raw, cfg, ol, reindex, stats, progress)
        db.set_state(conn, "index_last_ts", str(db.now()))
    meta = index_meta(reindex=reindex, progress=progress)
    for key, val in meta.items():
        if key in stats:
            stats[key] += val
    from . import transcripts_index
    tx = transcripts_index.index_transcripts(reindex=reindex)
    for key, val in tx.items():
        if key in stats:
            stats[key] += val
    if reindex:
        with db.session() as conn:
            db.rebuild_fts(conn)
    return stats
