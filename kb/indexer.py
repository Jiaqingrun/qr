from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

from . import config, db
from .ollama_client import Ollama
from .vectors import to_blob


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


def _iter_files(roots, exclude, exts, max_bytes):
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in exclude and not d.startswith(".")]
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
            h = hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()
            path_s = str(p)
            row = conn.execute("SELECT id, hash FROM documents WHERE path=?", (path_s,)).fetchone()
            if row and row["hash"] == h and not reindex:
                stats["skipped"] += 1
                continue
            rel = p.relative_to(root)
            project = rel.parts[0] if len(rel.parts) > 1 else root.name
            chunks = _chunk(raw, int(cfg["chunk_chars"]), int(cfg["chunk_overlap"]))
            if not chunks:
                continue
            if row:
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
                conn.execute(
                    "INSERT INTO chunks(doc_id,ordinal,text,dim,embedding) VALUES(?,?,?,?,?)",
                    (doc_id, i, ch, len(emb), to_blob(emb)),
                )
                stats["chunks"] += 1
            stats["files"] += 1
            if progress:
                progress(path_s, len(chunks))
            conn.commit()
    return stats
