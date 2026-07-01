"""历史 project 名归一化（legacy 别名、索引子目录误标、Cursor 空标签补全）。"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from . import config, cursor_archive, db, timeline_search, workspace


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


def audit_project_labels(
    conn: sqlite3.Connection,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """统计 project 标签噪声：空 Cursor 标签、索引子目录误标、legacy 残留。"""
    cfg = cfg or config.load_config()
    empty_cursor = int(
        conn.execute(
            "SELECT COUNT(*) c FROM events WHERE source='cursor' "
            "AND (project IS NULL OR project='')",
        ).fetchone()["c"]
    )
    empty_by_slug: Counter[str] = Counter()
    for row in conn.execute(
        "SELECT meta FROM events WHERE source='cursor' "
        "AND (project IS NULL OR project='')",
    ).fetchall():
        slug = ""
        try:
            slug = str(json.loads(row["meta"] or "{}").get("cursor_slug") or "")
        except json.JSONDecodeError:
            pass
        empty_by_slug[slug or "(无 slug)"] += 1

    legacy_rows: list[dict[str, Any]] = []
    legacy_keys = list(workspace.LEGACY_PROJECT_ALIASES.keys())
    if legacy_keys:
        ph = ",".join("?" * len(legacy_keys))
        for table in ("events", "documents", "prompt_guide_fragments", "prompt_guides"):
            try:
                n = int(
                    conn.execute(
                        f"SELECT COUNT(*) c FROM {table} WHERE project IN ({ph})",
                        legacy_keys,
                    ).fetchone()["c"]
                )
            except sqlite3.OperationalError:
                n = 0
            if n:
                legacy_rows.append({"table": table, "count": n})

    root = workspace.workspace_root(cfg)
    project_dirs: list[tuple[str, Path]] = []
    for cat in workspace.categories(cfg):
        project_dirs.extend(workspace.iter_category_project_dirs(root, cat))

    fragmented: Counter[tuple[str, str]] = Counter()
    junk_docs: Counter[str] = Counter()
    for row in conn.execute("SELECT path, project FROM documents").fetchall():
        stored = (row["project"] or "").strip()
        if not stored:
            continue
        path_s = row["path"] or ""
        target = ""
        try:
            resolved = Path(path_s).resolve()
            best: str | None = None
            best_len = -1
            for pid, proj_dir in project_dirs:
                try:
                    resolved.relative_to(proj_dir.resolve())
                except (ValueError, OSError):
                    continue
                if len(pid) > best_len:
                    best_len = len(pid)
                    best = pid
            target = best or workspace.index_project_for_path(path_s, stored, cfg)
        except OSError:
            target = workspace.index_project_for_path(path_s, stored, cfg)
        if target and stored != target:
            fragmented[(stored, target)] += 1
        elif not workspace.is_listable_project_id(stored, cfg):
            if stored not in ("qr-config", "qr-standards"):
                junk_docs[stored] += 1

    return {
        "empty_cursor_events": empty_cursor,
        "empty_cursor_by_slug": dict(empty_by_slug.most_common(12)),
        "legacy_remaining": legacy_rows,
        "fragmented_documents": sum(fragmented.values()),
        "fragmented_samples": [
            {"from": a, "to": b, "count": c}
            for (a, b), c in fragmented.most_common(8)
        ],
        "junk_document_projects": dict(junk_docs.most_common(8)),
    }


def preview_document_projects(
    conn: sqlite3.Connection,
    cfg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = cfg or config.load_config()
    counts: Counter[tuple[str, str]] = Counter()
    for row in conn.execute("SELECT path, project FROM documents").fetchall():
        stored = (row["project"] or "").strip()
        target = workspace.index_project_for_path(row["path"] or "", stored, cfg)
        if stored and target and stored != target:
            counts[(stored, target)] += 1
    return [
        {"from": a, "to": b, "documents": c}
        for (a, b), c in counts.most_common(50)
    ]


def normalize_document_projects(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    cfg: dict[str, Any] | None = None,
) -> dict[str, int]:
    """将 documents/chunks/FTS/symbols 的 project 对齐到工作区项目根。"""
    cfg = cfg or config.load_config()
    stats = {"documents": 0, "chunks": 0, "symbols": 0}
    updates: list[tuple[int, str, str, str]] = []
    for row in conn.execute("SELECT id, path, project FROM documents").fetchall():
        doc_id = int(row["id"])
        path = row["path"] or ""
        stored = (row["project"] or "").strip()
        target = workspace.index_project_for_path(path, stored, cfg)
        if not target or target == stored:
            continue
        updates.append((doc_id, path, stored, target))

    stats["documents"] = len(updates)
    if dry_run:
        if updates:
            stats["chunks"] = int(
                conn.execute(
                    "SELECT COUNT(*) c FROM chunks WHERE doc_id IN ({})".format(
                        ",".join("?" * len(updates)),
                    ),
                    [u[0] for u in updates],
                ).fetchone()["c"]
            )
        return stats

    for doc_id, path, _stored, target in updates:
        conn.execute("UPDATE documents SET project=? WHERE id=?", (target, doc_id))
        stats["chunks"] += _reindex_document(conn, doc_id)
        path_s = str(Path(path).resolve()) if path else path
        if path_s:
            cur = conn.execute(
                "UPDATE symbols SET project=? WHERE path=?",
                (target, path_s),
            )
            stats["symbols"] += int(cur.rowcount)
    if updates:
        conn.commit()
    return stats


def _session_id_from_archive_relpath(relpath: str) -> str:
    parts = (relpath or "").replace("\\", "/").strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "cursor_chats":
        return parts[1]
    return ""


def backfill_cursor_empty_projects(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    cfg: dict[str, Any] | None = None,
) -> dict[str, int]:
    """为 project 为空的 Cursor 事件补全标签（注册表 / 归档 meta）。"""
    cfg = cfg or config.load_config()
    workspace.sync_cursor_roots_registry(cfg, persist=not dry_run)
    stats = {"updated": 0, "skipped": 0}
    rows = conn.execute(
        "SELECT uid, project, meta, title, content FROM events "
        "WHERE source='cursor' AND (project IS NULL OR project='')",
    ).fetchall()
    for row in rows:
        meta_raw = row["meta"] or ""
        try:
            meta = json.loads(meta_raw) if meta_raw else {}
        except json.JSONDecodeError:
            meta = {}
        slug = str(meta.get("cursor_slug") or "")
        new_proj: str | None = None
        if slug:
            pid, needs = workspace.resolve_cursor_project(slug, cfg)
            if pid and not needs:
                new_proj = pid
        if not new_proj:
            sid = _session_id_from_archive_relpath(str(meta.get("archive_path") or ""))
            if sid:
                meta_path = cursor_archive.archive_root() / sid / "meta.json"
                if meta_path.is_file():
                    try:
                        archived = json.loads(meta_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        archived = {}
                    ap = workspace.canonical_project_id(archived.get("project"), cfg)
                    if ap and workspace.is_listable_project_id(ap, cfg):
                        new_proj = ap
        if not new_proj:
            stats["skipped"] += 1
            continue
        if dry_run:
            stats["updated"] += 1
            continue
        conn.execute("UPDATE events SET project=? WHERE uid=?", (new_proj, row["uid"]))
        timeline_search.index_event(
            conn,
            uid=row["uid"],
            source="cursor",
            project=new_proj,
            title=row["title"] or "",
            content=row["content"] or "",
        )
        stats["updated"] += 1
    if not dry_run and stats["updated"]:
        conn.commit()
    return stats


def run_full_normalize(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    only: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """legacy 别名 + 索引 project + Cursor 空标签，一步完成。"""
    cfg = cfg or config.load_config()
    legacy = migrate_legacy_projects(conn, dry_run=dry_run, only=only)
    docs = normalize_document_projects(conn, dry_run=dry_run, cfg=cfg)
    cursor = backfill_cursor_empty_projects(conn, dry_run=dry_run, cfg=cfg)
    return {"legacy": legacy, "documents": docs, "cursor": cursor}
