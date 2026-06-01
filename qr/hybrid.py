from __future__ import annotations

import re
import sqlite3

_RRF_K = 60


def _fts_query(text: str) -> str | None:
    terms = [t for t in re.split(r"\W+", text) if len(t) > 1][:10]
    if not terms:
        return None
    return " OR ".join(f'"{t}"' for t in terms)


def fts_search(
    conn: sqlite3.Connection,
    question: str,
    k: int = 24,
    project: str | None = None,
    category: str | None = None,
) -> list[dict]:
    match = _fts_query(question)
    if not match:
        return []
    try:
        rows = conn.execute(
            "SELECT chunk_id, bm25(chunks_fts) AS rank FROM chunks_fts "
            "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
            (match, k * 3),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[dict] = []
    for r in rows:
        info = conn.execute(
            "SELECT c.text, d.path, d.project FROM chunks c "
            "JOIN documents d ON c.doc_id=d.id WHERE c.id=?",
            (r["chunk_id"],),
        ).fetchone()
        if not info:
            continue
        from . import query as _q

        if not _q._project_filter_match(
            info["path"] or "", project, info["project"], category=category,
        ):
            continue
        out.append({
            "chunk_id": int(r["chunk_id"]),
            "score": 1.0 / (1.0 + max(0.0, float(r["rank"]))),
            "path": info["path"],
            "project": info["project"],
            "text": info["text"],
        })
        if len(out) >= k:
            break
    return out


def _hit_key(h: dict) -> str:
    if h.get("chunk_id"):
        return f"id:{h['chunk_id']}"
    return f"p:{h.get('path','')}:{hash(h.get('text','')[:80])}"


def rrf_merge(
    vec_hits: list[dict],
    fts_hits: list[dict],
    limit: int = 6,
) -> list[dict]:
    fused: dict[str, dict] = {}
    for rank, h in enumerate(vec_hits):
        key = _hit_key(h)
        entry = fused.setdefault(key, {**h, "rrf": 0.0})
        entry["rrf"] += 1.0 / (_RRF_K + rank + 1)
        entry["vec_score"] = h.get("score", 0.0)
    for rank, h in enumerate(fts_hits):
        key = _hit_key(h)
        entry = fused.setdefault(key, {**h, "rrf": 0.0})
        entry["rrf"] += 1.0 / (_RRF_K + rank + 1)
        entry["fts_score"] = h.get("score", 0.0)
        for field in ("path", "project", "text", "chunk_id"):
            if field not in entry and field in h:
                entry[field] = h[field]
    merged = list(fused.values())
    merged.sort(key=lambda x: x["rrf"], reverse=True)
    out = []
    for h in merged[:limit]:
        h["score"] = float(h["rrf"])
        out.append(h)
    return out
