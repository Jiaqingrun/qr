"""历史 project 名归一化（如 qr → dev/qr）。"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from . import cursor_archive, db, timeline_search, workspace


def legacy_aliases() -> dict[str, str]:
    return dict(workspace.LEGACY_PROJECT_ALIASES)


def _reindex_event(conn: sqlite3.Connection, uid: str) -> None:
    row = conn.execute(
        "SELECT uid, source, project, title, content FROM events WHERE uid=?",
        (uid,),
    ).fetchone()
    if not row:
        return
    timeline_search.index_event(
        conn,
        uid=row["uid"],
        source=row["source"],
        project=row["project"],
        title=row["title"] or "",
        content=row["content"] or "",
    )


def _reindex_document(conn: sqlite3.Connection, doc_id: int) -> int:
    rows = conn.execute(
        "SELECT c.id, c.text, d.path, d.project FROM chunks c "
        "JOIN documents d ON d.id = c.doc_id WHERE d.id=?",
        (doc_id,),
    ).fetchall()
    for r in rows:
        db.fts_index_chunk(
            conn,
            chunk_id=r["id"],
            path=r["path"],
            project=r["project"],
            text=r["text"],
        )
    return len(rows)


def migrate_legacy_projects(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    only: str | None = None,
) -> dict[str, int]:
    """将 legacy project 批量改为 category/name 规范 ID。"""
    targets = dict(workspace.LEGACY_PROJECT_ALIASES)
    if only:
        canon = workspace.canonical_project_id(only) or only.strip()
        targets = {k: v for k, v in targets.items() if v == canon}
        if not targets and only.strip() in workspace.LEGACY_PROJECT_ALIASES:
            k = only.strip()
            targets = {k: workspace.LEGACY_PROJECT_ALIASES[k]}

    stats: dict[str, int] = {
        "events": 0,
        "events_fts": 0,
        "documents": 0,
        "chunks": 0,
        "prompt_fragments": 0,
        "prompt_guides": 0,
        "cursor_archives": 0,
        "note_titles": 0,
    }
    if not targets:
        return stats

    legacy_keys = list(targets.keys())
    ph = ",".join("?" * len(legacy_keys))

    event_uids = [
        r["uid"]
        for r in conn.execute(
            f"SELECT uid FROM events WHERE project IN ({ph})",
            legacy_keys,
        ).fetchall()
    ]
    stats["events"] = len(event_uids)

    doc_ids = [
        int(r["id"])
        for r in conn.execute(
            f"SELECT id FROM documents WHERE project IN ({ph})",
            legacy_keys,
        ).fetchall()
    ]
    stats["documents"] = len(doc_ids)

    stats["prompt_fragments"] = int(
        conn.execute(
            f"SELECT COUNT(*) c FROM prompt_guide_fragments WHERE project IN ({ph})",
            legacy_keys,
        ).fetchone()["c"]
    )
    stats["prompt_guides"] = int(
        conn.execute(
            f"SELECT COUNT(*) c FROM prompt_guides WHERE project IN ({ph})",
            legacy_keys,
        ).fetchone()["c"]
    )

    root = cursor_archive.archive_root()
    archive_paths: list[Path] = []
    if root.is_dir():
        for meta in root.glob("*/meta.json"):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("project") in targets:
                archive_paths.append(meta)
    stats["cursor_archives"] = len(archive_paths)

    note_rows = [
        r
        for r in conn.execute(
            "SELECT uid, project, title, content FROM events "
            "WHERE source='note' AND uid LIKE 'note:cursor-summary:%'",
        ).fetchall()
        if (r["project"] or "") in targets
    ]
    stats["note_titles"] = len(note_rows)

    if dry_run:
        stats["events_fts"] = stats["events"]
        if doc_ids:
            stats["chunks"] = int(
                conn.execute(
                    f"SELECT COUNT(*) c FROM chunks WHERE doc_id IN ({','.join('?' * len(doc_ids))})",
                    doc_ids,
                ).fetchone()["c"]
            )
        return stats

    for legacy, canon in targets.items():
        conn.execute("UPDATE events SET project=? WHERE project=?", (canon, legacy))
        conn.execute("UPDATE documents SET project=? WHERE project=?", (canon, legacy))
        conn.execute(
            "UPDATE prompt_guide_fragments SET project=? WHERE project=?",
            (canon, legacy),
        )
        conn.execute(
            "UPDATE prompt_guides SET project=? WHERE project=?",
            (canon, legacy),
        )

    for uid in event_uids:
        _reindex_event(conn, uid)
    stats["events_fts"] = len(event_uids)

    chunk_total = 0
    for doc_id in doc_ids:
        chunk_total += _reindex_document(conn, doc_id)
    stats["chunks"] = chunk_total

    for meta in archive_paths:
        data = json.loads(meta.read_text(encoding="utf-8"))
        old = data.get("project")
        if old in targets:
            data["project"] = targets[old]
            meta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    for row in note_rows:
        old = row["project"] or ""
        canon = targets[old]
        conn.execute(
            "UPDATE events SET project=?, title=? WHERE uid=?",
            (canon, f"Cursor 会话摘要 · {canon}", row["uid"]),
        )
        note_body = ""
        if row["content"]:
            p = Path(row["content"])
            if p.is_file():
                try:
                    note_body = p.read_text(encoding="utf-8")
                    note_body = re.sub(
                        r"^# Cursor 会话摘要 · \S+",
                        f"# Cursor 会话摘要 · {canon}",
                        note_body,
                        count=1,
                    )
                    p.write_text(note_body, encoding="utf-8")
                except OSError:
                    pass
        timeline_search.index_event(
            conn,
            uid=row["uid"],
            source="note",
            project=canon,
            title=f"Cursor 会话摘要 · {canon}",
            content=note_body,
        )

    conn.commit()
    return stats


def preview_legacy_projects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for legacy, canon in workspace.LEGACY_PROJECT_ALIASES.items():
        n = int(
            conn.execute(
                "SELECT COUNT(*) c FROM events WHERE project=?",
                (legacy,),
            ).fetchone()["c"]
        )
        if n:
            out.append({"legacy": legacy, "target": canon, "events": n})
    return out
