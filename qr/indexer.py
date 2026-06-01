from __future__ import annotations

import hashlib
import os
from pathlib import Path

from . import config, db
from .ollama_client import Ollama
from .vectors import to_blob

META_FILES: list[tuple[Path, str]] = [
    (config.CONFIG_PATH, "qr-config"),
    (config.STANDARDS_PATH, "qr-standards"),
]


def _chunk(text: str, size: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        nl = text.rfind("\n", start + size // 2, end)
        if nl != -1 and end < n:
            end = nl
        chunks.append(text[start:end].strip())
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


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
    chunks = _chunk(raw, int(cfg["chunk_chars"]), int(cfg["chunk_overlap"]))
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
    stats["files"] += 1
    if progress:
        progress(path_s, len(chunks))
    conn.commit()


def _iter_files(roots, exclude, exts, max_bytes):
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in exclude
                           and not d.startswith(".") and not d.endswith(".egg-info")]
            for fn in filenames:
                p = Path(dirpath) / fn
                if p.suffix.lower() not in exts:
                    continue
                try:
                    if p.stat().st_size > max_bytes:
                        continue
                except OSError:
                    continue
                yield root, p


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


def index(reindex: bool = False, progress=None) -> dict[str, int]:
    cfg = config.load_config()
    roots = config.expand_paths(cfg["index_roots"])
    exclude = set(cfg["index_exclude_dirs"])
    exts = set(cfg["index_extensions"])
    max_bytes = int(cfg["max_file_bytes"])
    ol = Ollama()

    stats = {"files": 0, "chunks": 0, "skipped": 0}
    with db.session() as conn:
        for root, p in _iter_files(roots, exclude, exts, max_bytes):
            try:
                raw = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            from . import workspace

            project = workspace.project_from_path(p, root)
            _index_document(conn, p, project, raw, cfg, ol, reindex, stats, progress)
    meta = index_meta(reindex=reindex, progress=progress)
    for key in stats:
        stats[key] += meta[key]
    from . import transcripts_index
    tx = transcripts_index.index_transcripts(reindex=reindex)
    for key in stats:
        stats[key] += tx[key]
    if reindex:
        with db.session() as conn:
            db.rebuild_fts(conn)
    return stats
