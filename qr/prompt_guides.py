"""引导语：从 Cursor 问话自动采集、分类、合并为可复用完整引导语。"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import config, db, timeutil, workspace
from . import cursor_prompt_time as cpt
from . import cursor_session_title as cst

ORIGIN_AUTO = "auto"
ORIGIN_MANUAL = "manual"
ORIGIN_MERGED = "merged"

TYPE_ORIGIN_AUTO = "auto"
TYPE_ORIGIN_MANUAL = "manual"

_DISMISS_PREFIX = "pg_dismiss:"
_SESSION_DISMISS_PREFIX = "pg_dismiss_session:"

_ARCHIVE_PATH_RE = re.compile(
    r"(/cursor_chats/|agent-transcripts/|\.cursor/projects/)|^q\d+\.md$",
    re.I,
)


def _is_archive_path(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if t.endswith(".md") and ("/" in t or t.startswith("q")):
        return True
    return bool(_ARCHIVE_PATH_RE.search(t))


def _resolve_fragment_query(
    *,
    content: str,
    event_uid: str | None = None,
    event_title: str | None = None,
    cursor_session_id: str | None = None,
    query_index: int | None = None,
) -> str:
    """把归档路径还原为真实问话（events.title / 转录 / 归档 md）。"""
    raw = (content or "").strip()
    if raw and not _is_archive_path(raw):
        if "Cursor 对话提问" in raw:
            parts = raw.split("\n\n", 1)
            return parts[1].strip() if len(parts) > 1 else raw
        return raw
    title = (event_title or "").strip()
    if title and not _is_archive_path(title):
        return title
    sid, qidx = cursor_session_id, query_index
    parsed = cpt.parse_event_uid(event_uid or "")
    if parsed:
        sid, qidx = parsed
    if sid and qidx is not None:
        turn = cpt.resolve_cursor_turn(
            sid,
            int(qidx),
            event_uid=event_uid,
            query_text=title or raw,
        )
        q = (turn.get("query") or "").strip()
        if q and not _is_archive_path(q):
            return q
    return title or raw


def _fragment_preview(text: str, max_len: int = 100) -> str:
    line = (text or "").strip().splitlines()[0].strip()
    if not line or _is_archive_path(line):
        return ""
    return line[:max_len]


def _dismiss_key(event_uid: str) -> str:
    return f"{_DISMISS_PREFIX}{event_uid}"


def _is_dismissed(conn: sqlite3.Connection, event_uid: str) -> bool:
    return bool(db.get_state(conn, _dismiss_key(event_uid)))


def _dismiss_events(conn: sqlite3.Connection, event_uids: list[str]) -> None:
    rows = [(_dismiss_key(uid), "1") for uid in event_uids if uid]
    if not rows:
        return
    conn.executemany(
        "INSERT INTO state(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        rows,
    )


def _shield_states(conn: sqlite3.Connection, keys: list[str]) -> None:
    rows = [(k, "1") for k in keys if k]
    if not rows:
        return
    conn.executemany(
        "INSERT INTO state(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        rows,
    )


def _session_dismiss_key(session_id: str) -> str:
    return f"{_SESSION_DISMISS_PREFIX}{session_id}"


def is_session_dismissed(conn: sqlite3.Connection, session_id: str) -> bool:
    sid = (session_id or "").strip()
    if not sid or sid == "unknown":
        return False
    return bool(db.get_state(conn, _session_dismiss_key(sid)))


def _shield_session(conn: sqlite3.Connection, session_id: str) -> None:
    sid = (session_id or "").strip()
    if sid and sid != "unknown":
        _shield_states(conn, [_session_dismiss_key(sid)])


def _shield_event_uids(conn: sqlite3.Connection, event_uids: list[str]) -> int:
    """屏蔽问话：写入 dismiss，并从时间线/事件中移除（不删 Cursor 原文件）。"""
    uids = [str(u) for u in event_uids if u]
    if not uids:
        return 0
    from . import timeline_search

    _dismiss_events(conn, uids)
    ph = ",".join("?" * len(uids))
    cur = conn.execute(f"DELETE FROM events WHERE uid IN ({ph})", uids)
    removed = int(cur.rowcount)
    for uid in uids:
        timeline_search.remove_event(conn, uid)
    return removed

DEFAULT_TYPES: list[dict[str, str]] = [
    {"name": "功能开发", "slug": "feature", "description": "实现新功能、加接口、写业务逻辑"},
    {"name": "排错调试", "slug": "debug", "description": "报错、异常、行为不符合预期"},
    {"name": "代码理解", "slug": "explain", "description": "解释原理、梳理流程、读代码"},
    {"name": "重构优化", "slug": "refactor", "description": "结构调整、性能、可维护性"},
    {"name": "测试", "slug": "test", "description": "单测、集成测、用例设计"},
    {"name": "文档", "slug": "docs", "description": "README、注释、说明文档"},
    {"name": "配置运维", "slug": "ops", "description": "部署、环境、CI、launchd"},
    {"name": "规范治理", "slug": "governance", "description": "规范、合规、工作区、知识库本身"},
    {"name": "通用", "slug": "general", "description": "未命中更细分类时的默认"},
]

_CLASSIFY_RULES: list[tuple[str, list[str]]] = [
    ("debug", [r"报错", r"错误", r"异常", r"bug", r"失败", r"不工作", r"修复", r"排查", r"堆栈", r"crash"]),
    ("refactor", [r"重构", r"优化", r"整理", r"抽取", r"简化", r"迁移"]),
    ("explain", [r"解释", r"是什么", r"怎么工作", r"原理", r"梳理", r"理解", r"讲讲"]),
    ("test", [r"测试", r"单测", r"pytest", r"用例", r"coverage"]),
    ("docs", [r"文档", r"readme", r"注释", r"说明书写"]),
    ("ops", [r"部署", r"docker", r"launchd", r"配置", r"环境", r"端口", r"nginx", r"ci"]),
    ("governance", [r"规范", r"知识库", r"qr ", r"workspace", r"合规", r"索引", r"引导语"]),
    ("feature", [r"实现", r"添加", r"新增", r"写一个", r"支持", r"接入", r"开发"]),
]


def _slugify(name: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", name.strip().lower())
    return s[:48].strip("-") or "type"


def _row_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS prompt_guide_types (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            slug        TEXT NOT NULL UNIQUE,
            description TEXT,
            type_origin TEXT NOT NULL DEFAULT 'manual',
            created_at  INTEGER NOT NULL,
            updated_at  INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS prompt_guides (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            body        TEXT NOT NULL,
            type_id     INTEGER,
            origin      TEXT NOT NULL,
            project     TEXT,
            tags        TEXT,
            meta        TEXT,
            created_at  INTEGER NOT NULL,
            updated_at  INTEGER NOT NULL,
            FOREIGN KEY(type_id) REFERENCES prompt_guide_types(id)
        );
        CREATE INDEX IF NOT EXISTS idx_prompt_guides_updated ON prompt_guides(updated_at);
        CREATE INDEX IF NOT EXISTS idx_prompt_guides_type ON prompt_guides(type_id);
        CREATE TABLE IF NOT EXISTS prompt_guide_fragments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            event_uid       TEXT UNIQUE,
            content         TEXT NOT NULL,
            project         TEXT,
            type_id         INTEGER,
            type_origin     TEXT NOT NULL DEFAULT 'auto',
            fragment_origin TEXT NOT NULL DEFAULT 'auto',
            guide_id        INTEGER,
            classify_note   TEXT,
            ts              INTEGER NOT NULL,
            created_at      INTEGER NOT NULL,
            FOREIGN KEY(type_id) REFERENCES prompt_guide_types(id),
            FOREIGN KEY(guide_id) REFERENCES prompt_guides(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_pgf_inbox ON prompt_guide_fragments(guide_id);
        CREATE INDEX IF NOT EXISTS idx_pgf_ts ON prompt_guide_fragments(ts);
        CREATE INDEX IF NOT EXISTS idx_pgf_session ON prompt_guide_fragments(cursor_session_id);
        """
    )
    db._ensure_column(conn, "prompt_guide_fragments", "cursor_session_id", "TEXT")
    db._ensure_column(conn, "prompt_guide_fragments", "query_index", "INTEGER")
    db._ensure_column(conn, "prompt_guide_fragments", "ts_estimated", "INTEGER DEFAULT 0")
    db._ensure_column(conn, "prompt_guide_fragments", "transcript_mtime", "INTEGER")
    seed_types(conn)


def _migrate_fragment_row(conn: sqlite3.Connection, fragment_id: int, event_uid: str) -> None:
    parsed = cpt.parse_event_uid(event_uid)
    if not parsed:
        return
    session_id, qidx = parsed
    conn.execute(
        "UPDATE prompt_guide_fragments SET cursor_session_id=?, query_index=? WHERE id=?",
        (session_id, qidx, fragment_id),
    )


def _backfill_fragment_cursor_ids(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT id, event_uid FROM prompt_guide_fragments "
        "WHERE event_uid IS NOT NULL AND TRIM(event_uid) != ''",
    ).fetchall()
    n = 0
    for row in rows:
        before = conn.execute(
            "SELECT cursor_session_id, query_index FROM prompt_guide_fragments WHERE id=?",
            (row["id"],),
        ).fetchone()
        _migrate_fragment_row(conn, row["id"], row["event_uid"])
        after = conn.execute(
            "SELECT cursor_session_id, query_index FROM prompt_guide_fragments WHERE id=?",
            (row["id"],),
        ).fetchone()
        if before != after:
            n += 1
    return n


def repair_inbox_timestamps(conn: sqlite3.Connection) -> dict[str, int]:
    """从 Cursor 转录重算收件箱碎片时间（并尽量同步 events.ts）。"""
    ensure_schema(conn)
    cpt.clear_transcript_cache()
    _backfill_fragment_cursor_ids(conn)
    rows = conn.execute(
        "SELECT id, event_uid FROM prompt_guide_fragments WHERE guide_id IS NULL",
    ).fetchall()
    updated = exact = 0
    for row in rows:
        uid = row["event_uid"]
        parsed = cpt.parse_event_uid(uid or "")
        if not parsed:
            continue
        session_id, qidx = parsed
        ts, estimated, _ = cpt.resolve_query_time(session_id, qidx)
        if ts <= 0:
            continue
        jsonl = cpt._transcript_map().get(session_id)
        mtime = int(jsonl.stat().st_mtime) if jsonl and jsonl.exists() else None
        conn.execute(
            "UPDATE prompt_guide_fragments SET ts=?, ts_estimated=?, cursor_session_id=?, "
            "query_index=?, transcript_mtime=? WHERE id=?",
            (ts, 1 if estimated else 0, session_id, qidx, mtime, row["id"]),
        )
        conn.execute("UPDATE events SET ts=? WHERE uid=?", (ts, uid))
        meta_row = conn.execute("SELECT meta FROM events WHERE uid=?", (uid,)).fetchone()
        if meta_row:
            try:
                meta = json.loads(meta_row["meta"] or "{}")
            except json.JSONDecodeError:
                meta = {}
            meta["ts_estimated"] = bool(estimated)
            conn.execute(
                "UPDATE events SET meta=? WHERE uid=?",
                (json.dumps(meta, ensure_ascii=False), uid),
            )
        updated += 1
        if not estimated:
            exact += 1
    conn.commit()
    query_repair = repair_inbox_queries(conn)
    return {
        "updated": updated,
        "exact": exact,
        "estimated": updated - exact,
        "query_repair": query_repair,
    }


def seed_types(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM prompt_guide_types LIMIT 1").fetchone():
        return
    now = db.now()
    for t in DEFAULT_TYPES:
        conn.execute(
            "INSERT OR IGNORE INTO prompt_guide_types"
            "(name, slug, description, type_origin, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (t["name"], t["slug"], t["description"], TYPE_ORIGIN_AUTO, now, now),
        )


def _type_by_slug(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM prompt_guide_types WHERE slug=?", (slug,),
    ).fetchone()


def classify_text_conn(conn: sqlite3.Connection, text: str) -> tuple[int, str, float]:
    blob = text.lower()
    best_slug = "general"
    best_score = 0
    for slug, patterns in _CLASSIFY_RULES:
        score = sum(1 for p in patterns if re.search(p, blob, re.I))
        if score > best_score:
            best_score = score
            best_slug = slug
    confidence = min(1.0, 0.35 + best_score * 0.2) if best_score else 0.25
    row = _type_by_slug(conn, best_slug) or _type_by_slug(conn, "general")
    tid = int(row["id"]) if row else 1
    note = f"rule:{best_slug} score={best_score}"
    return tid, note, confidence


def get_or_create_type(
    conn: sqlite3.Connection,
    name: str,
    *,
    description: str = "",
    type_origin: str = TYPE_ORIGIN_MANUAL,
) -> int:
    name = name.strip()
    if not name:
        raise ValueError("类型名称不能为空")
    slug = _slugify(name)
    row = conn.execute(
        "SELECT id FROM prompt_guide_types WHERE name=? OR slug=?",
        (name, slug),
    ).fetchone()
    if row:
        return int(row["id"])
    now = db.now()
    cur = conn.execute(
        "INSERT INTO prompt_guide_types(name, slug, description, type_origin, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?)",
        (name, slug, description, type_origin, now, now),
    )
    return int(cur.lastrowid)


def list_types(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT t.*, "
        "(SELECT COUNT(*) FROM prompt_guides g WHERE g.type_id=t.id) AS guide_count, "
        "(SELECT COUNT(*) FROM prompt_guide_fragments f WHERE f.type_id=t.id AND f.guide_id IS NULL) "
        "AS inbox_count "
        "FROM prompt_guide_types t ORDER BY t.name"
    ).fetchall()
    return [dict(r) for r in rows]


def sync_cursor_inbox(conn: sqlite3.Connection) -> dict[str, int]:
    """把 events 表中 cursor 来源的提问同步为引导语碎片（仅新增）。"""
    ensure_schema(conn)
    cfg = config.load_config()
    if not cfg.get("prompt_guides_auto_sync", True):
        return {"new": 0, "skipped": 0}

    rows = conn.execute(
        "SELECT uid, ts, project, title, content FROM events WHERE source='cursor' "
        "ORDER BY ts DESC LIMIT 5000"
    ).fetchall()
    titles = cst.load_session_titles(cfg=cfg)
    new = skipped = excluded_title = 0
    for r in rows:
        uid = r["uid"]
        if not uid or not str(uid).startswith("cursor:"):
            skipped += 1
            continue
        if _is_dismissed(conn, uid):
            skipped += 1
            continue
        exists = conn.execute(
            "SELECT id FROM prompt_guide_fragments WHERE event_uid=?", (uid,),
        ).fetchone()
        if exists:
            skipped += 1
            continue
        parsed_uid = cpt.parse_event_uid(uid)
        if parsed_uid:
            session_id, _ = parsed_uid
            chat_title = titles.get(session_id, "")
            if not cst.should_include_in_prompt_guides(chat_title):
                excluded_title += 1
                continue
        content = _resolve_fragment_query(
            content=(r["content"] or "").strip(),
            event_uid=uid,
            event_title=(r["title"] or "").strip(),
        )
        if not content or _is_archive_path(content):
            skipped += 1
            continue
        type_id, note, conf = classify_text_conn(conn, content)
        now = db.now()
        session_id, qidx = "", 0
        parsed = cpt.parse_event_uid(uid)
        ts_val, ts_est = int(r["ts"]), 1
        transcript_mtime = None
        if parsed:
            session_id, qidx = parsed
            ts_val, ts_est, _ = cpt.resolve_query_time(session_id, qidx)
            if ts_val <= 0:
                ts_val = int(r["ts"])
            else:
                jsonl = cpt._transcript_map().get(session_id)
                if jsonl and jsonl.exists():
                    transcript_mtime = int(jsonl.stat().st_mtime)
        conn.execute(
            "INSERT INTO prompt_guide_fragments"
            "(event_uid, content, project, type_id, type_origin, fragment_origin, "
            "guide_id, classify_note, ts, ts_estimated, cursor_session_id, query_index, "
            "transcript_mtime, created_at) "
            "VALUES(?,?,?,?,?,?,NULL,?,?,?,?,?,?,?)",
            (
                uid,
                content,
                workspace.canonical_project_id(r["project"]) or r["project"],
                type_id,
                TYPE_ORIGIN_AUTO,
                ORIGIN_AUTO,
                json.dumps({"confidence": conf, "note": note}, ensure_ascii=False),
                ts_val,
                1 if ts_est else 0,
                session_id or None,
                qidx,
                transcript_mtime,
                now,
            ),
        )
        new += 1
    conn.commit()
    repair = repair_inbox_timestamps(conn)
    query_repair = repair_inbox_queries(conn)
    return {
        "new": new,
        "skipped": skipped,
        "excluded_by_session_title": excluded_title,
        "repair": repair,
        "query_repair": query_repair,
    }


def repair_inbox_queries(conn: sqlite3.Connection) -> dict[str, int]:
    """把碎片 content 中的归档路径回填为真实问话。"""
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT f.id, f.event_uid, f.content, f.cursor_session_id, f.query_index, "
        "e.title AS event_title FROM prompt_guide_fragments f "
        "LEFT JOIN events e ON e.uid=f.event_uid",
    ).fetchall()
    updated = 0
    for r in rows:
        old = (r["content"] or "").strip()
        if not _is_archive_path(old):
            continue
        new = _resolve_fragment_query(
            content=old,
            event_uid=r["event_uid"],
            event_title=r["event_title"],
            cursor_session_id=r["cursor_session_id"],
            query_index=r["query_index"],
        )
        if not new or _is_archive_path(new) or new == old:
            continue
        conn.execute(
            "UPDATE prompt_guide_fragments SET content=? WHERE id=?",
            (new, r["id"]),
        )
        updated += 1
    conn.commit()
    return {"updated": updated}


def list_fragments(
    conn: sqlite3.Connection,
    *,
    inbox_only: bool = True,
    type_id: int | None = None,
    project: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    ensure_schema(conn)
    where = ["f.guide_id IS NULL"] if inbox_only else ["1=1"]
    params: list[Any] = []
    if type_id:
        where.append("f.type_id=?")
        params.append(type_id)
    if project:
        pvals = workspace.project_filter_values(project)
        if pvals:
            ph = ",".join("?" * len(pvals))
            where.append(f"f.project IN ({ph})")
            params.extend(pvals)
    sql = (
        "SELECT f.*, e.title AS event_title, t.name AS type_name, t.slug AS type_slug, "
        "t.type_origin AS type_table_origin "
        "FROM prompt_guide_fragments f "
        "LEFT JOIN events e ON e.uid=f.event_uid "
        "LEFT JOIN prompt_guide_types t ON f.type_id=t.id "
        f"WHERE {' AND '.join(where)} ORDER BY f.ts DESC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    return [_enrich_fragment(dict(r)) for r in rows]


def list_inbox_groups(
    conn: sqlite3.Connection,
    *,
    type_id: int | None = None,
    project: str | None = None,
    session_ids: list[str] | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """按 Cursor 对话（transcript uuid）分组收件箱碎片。"""
    frags = list_fragments(
        conn, inbox_only=True, type_id=type_id, project=project, limit=limit, offset=0,
    )
    by_session: dict[str, list[dict]] = {}
    for f in frags:
        sid = f.get("cursor_session_id") or "unknown"
        by_session.setdefault(sid, []).append(f)
    groups: list[dict] = []
    for sid, items in by_session.items():
        items.sort(key=lambda x: (x.get("query_index") if x.get("query_index") is not None else 9999, x.get("ts") or 0))
        ts_list = [int(x["ts"]) for x in items if x.get("ts")]
        est_n = sum(1 for x in items if x.get("ts_estimated"))
        title = ""
        for item in items:
            preview = _fragment_preview(item.get("content") or "")
            if preview:
                title = preview
                break
        if not title:
            title = f"Cursor 对话 · {sid[:8]}"
        groups.append({
            "session_id": sid,
            "project": items[0].get("project") if items else "",
            "project_label": items[0].get("project_label") if items else "",
            "title": title,
            "fragment_count": len(items),
            "started_ts": min(ts_list) if ts_list else 0,
            "ended_ts": max(ts_list) if ts_list else 0,
            "estimated_count": est_n,
            "all_exact": est_n == 0 and bool(ts_list),
            "fragments": items,
        })
    groups.sort(key=lambda g: g["ended_ts"], reverse=True)
    if session_ids:
        allow = set(session_ids)
        groups = [g for g in groups if g["session_id"] in allow]
    return {
        "groups": groups,
        "session_total": len(by_session),
        "fragment_total": len(frags),
    }


def list_guides(
    conn: sqlite3.Connection,
    *,
    type_id: int | None = None,
    origin: str | None = None,
    project: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    ensure_schema(conn)
    where = ["1=1"]
    params: list[Any] = []
    if type_id:
        where.append("g.type_id=?")
        params.append(type_id)
    if origin:
        where.append("g.origin=?")
        params.append(origin)
    if project:
        pvals = workspace.project_filter_values(project)
        if pvals:
            ph = ",".join("?" * len(pvals))
            where.append(f"g.project IN ({ph})")
            params.extend(pvals)
    sql = (
        "SELECT g.*, t.name AS type_name, t.slug AS type_slug, t.type_origin AS type_table_origin, "
        "(SELECT COUNT(*) FROM prompt_guide_fragments f WHERE f.guide_id=g.id) AS fragment_count "
        "FROM prompt_guides g LEFT JOIN prompt_guide_types t ON g.type_id=t.id "
        f"WHERE {' AND '.join(where)} ORDER BY g.updated_at DESC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    return [_enrich_guide(dict(r)) for r in rows]


def get_guide(conn: sqlite3.Connection, guide_id: int) -> dict | None:
    row = conn.execute(
        "SELECT g.*, t.name AS type_name, t.slug AS type_slug, "
        "t.type_origin AS type_table_origin "
        "FROM prompt_guides g LEFT JOIN prompt_guide_types t ON g.type_id=t.id "
        "WHERE g.id=?",
        (guide_id,),
    ).fetchone()
    if not row:
        return None
    g = _enrich_guide(dict(row))
    frags = conn.execute(
        "SELECT f.*, e.title AS event_title, t.name AS type_name FROM prompt_guide_fragments f "
        "LEFT JOIN events e ON e.uid=f.event_uid "
        "LEFT JOIN prompt_guide_types t ON f.type_id=t.id "
        "WHERE f.guide_id=? ORDER BY f.ts",
        (guide_id,),
    ).fetchall()
    enriched: list[dict] = []
    for f in frags:
        fd = _enrich_fragment(dict(f))
        turn = cpt.resolve_cursor_turn(
            fd.get("cursor_session_id"),
            fd.get("query_index"),
            event_uid=fd.get("event_uid"),
            query_text=fd.get("content"),
        )
        fd["cursor_reply"] = turn.get("reply") or ""
        fd["reply_found"] = bool(turn.get("found"))
        if turn.get("found") and turn.get("query"):
            if not (fd.get("content") or "").strip() or _is_archive_path(fd.get("content") or ""):
                fd["content"] = turn["query"]
        enriched.append(fd)
    g["fragments"] = enriched
    return g


def _enrich_project_fields(d: dict) -> dict:
    raw = (d.get("project") or "").strip()
    if not raw:
        d["project"] = None
        d["project_label"] = None
        return d
    canon = workspace.canonical_project_id(raw)
    d["project"] = canon
    d["project_label"] = workspace.project_timeline_label(raw)
    return d


def _enrich_guide(d: dict) -> dict:
    _enrich_project_fields(d)
    d["badges"] = _guide_badges(d)
    if d.get("tags") and isinstance(d["tags"], str):
        try:
            d["tags"] = json.loads(d["tags"])
        except json.JSONDecodeError:
            d["tags"] = []
    if d.get("meta") and isinstance(d["meta"], str):
        try:
            d["meta"] = json.loads(d["meta"])
        except json.JSONDecodeError:
            d["meta"] = {}
    return d


def _enrich_fragment(d: dict) -> dict:
    _enrich_project_fields(d)
    uid = d.get("event_uid") or ""
    parsed = cpt.parse_event_uid(uid)
    if parsed:
        d["cursor_session_id"], d["query_index"] = parsed[0], parsed[1]
    ts = int(d.get("ts") or 0)
    est = bool(d.get("ts_estimated"))
    d["ts_estimated"] = est
    d["ts_iso"] = timeutil.format_local(ts) if ts > 0 else ""
    d["ts_label"] = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts > 0 else "—"
    )
    d["time_badge"] = (
        {"label": "推算", "kind": "estimated"}
        if est
        else {"label": "精确", "kind": "exact"}
    )
    d["badges"] = _fragment_badges(d)
    archive = d.get("content") if _is_archive_path(d.get("content") or "") else ""
    d["content"] = _resolve_fragment_query(
        content=d.get("content") or "",
        event_uid=d.get("event_uid"),
        event_title=d.get("event_title"),
        cursor_session_id=d.get("cursor_session_id"),
        query_index=d.get("query_index"),
    )
    if archive and not d.get("archive_path"):
        d["archive_path"] = archive
    return d


def _fragment_badges(d: dict) -> dict:
    badges = {
        "source": {"label": "Cursor 自动", "kind": "auto"},
        "classify": {
            "label": "自动分类" if d.get("type_origin") == TYPE_ORIGIN_AUTO else "手动分类",
            "kind": "auto" if d.get("type_origin") == TYPE_ORIGIN_AUTO else "manual",
        },
        "merge": {"label": "待合并", "kind": "inbox"},
    }
    tb = d.get("time_badge")
    if tb:
        badges["time"] = tb
    return badges


def _guide_badges(d: dict) -> dict:
    origin = d.get("origin") or ORIGIN_MANUAL
    labels = {
        ORIGIN_AUTO: ("自动收录", "auto"),
        ORIGIN_MANUAL: ("手动创建", "manual"),
        ORIGIN_MERGED: ("合并合成", "merged"),
    }
    src_label, src_kind = labels.get(origin, ("手动", "manual"))
    return {
        "source": {"label": src_label, "kind": src_kind},
        "classify": {
            "label": "自动分类" if d.get("type_table_origin") == TYPE_ORIGIN_AUTO else "手动类型",
            "kind": "auto" if d.get("type_table_origin") == TYPE_ORIGIN_AUTO else "manual",
        },
    }


def merge_fragments(
    conn: sqlite3.Connection,
    fragment_ids: list[int],
    *,
    title: str | None = None,
    type_id: int | None = None,
    type_name: str | None = None,
    project: str | None = None,
    tags: list[str] | None = None,
    body: str | None = None,
    refined: bool = False,
) -> dict:
    if len(fragment_ids) < 1:
        raise ValueError("请至少选择一条问话片段")
    ensure_schema(conn)
    placeholders = ",".join("?" * len(fragment_ids))
    frags = conn.execute(
        f"SELECT * FROM prompt_guide_fragments WHERE id IN ({placeholders}) "
        "AND guide_id IS NULL ORDER BY ts",
        fragment_ids,
    ).fetchall()
    if len(frags) != len(fragment_ids):
        raise ValueError("部分片段不存在或已合并")
    bodies = []
    event_uids = []
    projects = []
    for i, f in enumerate(frags, 1):
        bodies.append(f"## 片段 {i}\n\n{f['content']}")
        event_uids.append(f["event_uid"])
        if f["project"]:
            projects.append(f["project"])
    raw_body = "\n\n---\n\n".join(bodies)
    if body is None:
        body = raw_body
    if not title:
        first = frags[0]["content"].splitlines()[0][:80]
        title = first if len(frags) == 1 else f"合并引导语 · {first[:40]}…（{len(frags)}段）"
    if type_name and not type_id:
        type_id = get_or_create_type(conn, type_name, type_origin=TYPE_ORIGIN_MANUAL)
    if not type_id:
        type_id = int(frags[0]["type_id"]) if frags[0]["type_id"] else None
    proj = project or (max(set(projects), key=projects.count) if projects else None)
    now = db.now()
    meta: dict[str, Any] = {
        "merged_from": fragment_ids,
        "event_uids": event_uids,
        "fragment_count": len(frags),
    }
    if refined:
        meta["refined"] = True
    cur = conn.execute(
        "INSERT INTO prompt_guides(title, body, type_id, origin, project, tags, meta, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            title.strip(),
            body,
            type_id,
            ORIGIN_MERGED,
            proj,
            json.dumps(tags or [], ensure_ascii=False),
            json.dumps(meta, ensure_ascii=False),
            now,
            now,
        ),
    )
    guide_id = int(cur.lastrowid)
    for fid in fragment_ids:
        conn.execute(
            "UPDATE prompt_guide_fragments SET guide_id=?, fragment_origin=? WHERE id=?",
            (guide_id, ORIGIN_MERGED, fid),
        )
    conn.commit()
    _export_guide_markdown(guide_id, title, body, type_id, conn)
    return get_guide(conn, guide_id) or {"id": guide_id}


def update_guide_content(
    conn: sqlite3.Connection,
    guide_id: int,
    *,
    title: str,
    body: str,
    type_id: int | None = None,
    type_name: str | None = None,
    tags: list[str] | None = None,
    refined: bool = False,
) -> dict:
    ensure_schema(conn)
    row = conn.execute("SELECT id FROM prompt_guides WHERE id=?", (guide_id,)).fetchone()
    if not row:
        raise ValueError("引导语不存在")
    if type_name and not type_id:
        type_id = get_or_create_type(conn, type_name)
    now = db.now()
    existing = get_guide(conn, guide_id) or {}
    meta = dict(existing.get("meta") or {})
    if refined:
        meta["refined"] = True
        meta["refined_at"] = now
    conn.execute(
        "UPDATE prompt_guides SET title=?, body=?, type_id=COALESCE(?, type_id), "
        "tags=?, meta=?, updated_at=? WHERE id=?",
        (
            title.strip(),
            body.strip(),
            type_id,
            json.dumps(tags or [], ensure_ascii=False),
            json.dumps(meta, ensure_ascii=False),
            now,
            guide_id,
        ),
    )
    conn.commit()
    _export_guide_markdown(guide_id, title, body, type_id, conn)
    return get_guide(conn, guide_id) or {"id": guide_id}


def create_guide_manual(
    conn: sqlite3.Connection,
    title: str,
    body: str,
    *,
    type_id: int | None = None,
    type_name: str | None = None,
    project: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    ensure_schema(conn)
    if type_name and not type_id:
        type_id = get_or_create_type(conn, type_name)
    now = db.now()
    cur = conn.execute(
        "INSERT INTO prompt_guides(title, body, type_id, origin, project, tags, meta, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            title.strip(),
            body.strip(),
            type_id,
            ORIGIN_MANUAL,
            project,
            json.dumps(tags or [], ensure_ascii=False),
            json.dumps({}, ensure_ascii=False),
            now,
            now,
        ),
    )
    guide_id = int(cur.lastrowid)
    conn.commit()
    _export_guide_markdown(guide_id, title, body, type_id, conn)
    return get_guide(conn, guide_id) or {"id": guide_id}


def update_fragment_type(
    conn: sqlite3.Connection,
    fragment_id: int,
    *,
    type_id: int | None = None,
    type_name: str | None = None,
) -> None:
    if type_name and not type_id:
        type_id = get_or_create_type(conn, type_name)
    if not type_id:
        raise ValueError("需要 type_id 或 type_name")
    conn.execute(
        "UPDATE prompt_guide_fragments SET type_id=?, type_origin=? WHERE id=?",
        (type_id, TYPE_ORIGIN_MANUAL, fragment_id),
    )
    conn.commit()


def reclassify_inbox_auto(conn: sqlite3.Connection) -> int:
    frags = conn.execute(
        "SELECT id, content FROM prompt_guide_fragments WHERE guide_id IS NULL",
    ).fetchall()
    n = 0
    for f in frags:
        tid, note, conf = classify_text_conn(conn, f["content"])
        conn.execute(
            "UPDATE prompt_guide_fragments SET type_id=?, type_origin=?, classify_note=? "
            "WHERE id=?",
            (
                tid,
                TYPE_ORIGIN_AUTO,
                json.dumps({"confidence": conf, "note": note}, ensure_ascii=False),
                f["id"],
            ),
        )
        n += 1
    conn.commit()
    return n


def _purge_document_path(conn: sqlite3.Connection, file_path: Path) -> bool:
    """从向量/全文索引移除该路径对应文档。"""
    import sqlite3

    candidates = {str(file_path), str(file_path.resolve())}
    row = None
    for p in candidates:
        row = conn.execute("SELECT id FROM documents WHERE path=?", (p,)).fetchone()
        if row:
            break
    if not row:
        return False
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
    return True


def _transcript_paths_for_session(base: Path, session_id: str) -> list[Path]:
    if not base.exists() or not session_id or session_id == "unknown":
        return []
    return sorted(base.glob(f"*/agent-transcripts/*/{session_id}.jsonl"))


def delete_cursor_sessions(conn: sqlite3.Connection, session_ids: list[str]) -> dict[str, Any]:
    """从知识库屏蔽整段 Cursor 对话（收件箱+时间线）；保留本机转录与归档文件。"""
    if not session_ids:
        return {
            "sessions": 0,
            "fragments": 0,
            "events": 0,
            "shielded": True,
        }
    ensure_schema(conn)

    stats: dict[str, Any] = {
        "sessions": 0,
        "fragments": 0,
        "events": 0,
        "shielded": True,
    }
    seen: set[str] = set()
    for sid in session_ids:
        sid = (sid or "").strip()
        if not sid or sid in seen or sid == "unknown":
            continue
        seen.add(sid)
        stats["sessions"] += 1
        _shield_session(conn, sid)

        frags = conn.execute(
            "SELECT event_uid FROM prompt_guide_fragments WHERE cursor_session_id=?",
            (sid,),
        ).fetchall()
        dismiss_uids = [str(r["event_uid"]) for r in frags if r["event_uid"]]
        stats["fragments"] += conn.execute(
            "SELECT COUNT(*) c FROM prompt_guide_fragments WHERE cursor_session_id=?",
            (sid,),
        ).fetchone()["c"]
        conn.execute(
            "DELETE FROM prompt_guide_fragments WHERE cursor_session_id=?", (sid,),
        )

        ev_rows = conn.execute(
            "SELECT uid FROM events WHERE source='cursor' AND "
            "(uid LIKE ? OR uid=?)",
            (f"cursor:{sid}:q%", f"cursor:{sid}"),
        ).fetchall()
        for r in ev_rows:
            uid = str(r["uid"])
            if uid not in dismiss_uids:
                dismiss_uids.append(uid)
        stats["events"] += _shield_event_uids(conn, dismiss_uids)

    cpt.clear_transcript_cache()
    return stats


def delete_fragments(conn: sqlite3.Connection, fragment_ids: list[int]) -> dict[str, int]:
    """屏蔽收件箱问话片段；已合并的不可删。保留 Cursor 原文件，后续同步不再显示。"""
    if not fragment_ids:
        return {"deleted": 0, "skipped": 0, "events": 0}
    ensure_schema(conn)
    placeholders = ",".join("?" * len(fragment_ids))
    rows = conn.execute(
        f"SELECT id, event_uid, guide_id FROM prompt_guide_fragments "
        f"WHERE id IN ({placeholders})",
        fragment_ids,
    ).fetchall()
    deleted = skipped = 0
    to_delete: list[int] = []
    dismiss_uids: list[str] = []
    for row in rows:
        if row["guide_id"] is not None:
            skipped += 1
            continue
        to_delete.append(int(row["id"]))
        if row["event_uid"]:
            dismiss_uids.append(str(row["event_uid"]))
    events_removed = 0
    if to_delete:
        ph2 = ",".join("?" * len(to_delete))
        conn.execute(
            f"DELETE FROM prompt_guide_fragments WHERE id IN ({ph2})",
            to_delete,
        )
        deleted = len(to_delete)
        events_removed = _shield_event_uids(conn, dismiss_uids)
    return {"deleted": deleted, "skipped": skipped, "events": events_removed}


def delete_guide(conn: sqlite3.Connection, guide_id: int) -> None:
    conn.execute(
        "UPDATE prompt_guide_fragments SET guide_id=NULL, fragment_origin=? "
        "WHERE guide_id=?",
        (ORIGIN_AUTO, guide_id),
    )
    conn.execute("DELETE FROM prompt_guides WHERE id=?", (guide_id,))


def _fragment_session_title(
    frag: sqlite3.Row | dict[str, Any],
    titles: dict[str, str],
) -> str:
    sid = str(frag["cursor_session_id"] or "").strip()
    if not sid:
        parsed = cpt.parse_event_uid(str(frag.get("event_uid") or ""))
        if parsed:
            sid = parsed[0]
    return titles.get(sid, "")


def _remove_guide_exports(conn: sqlite3.Connection, guide_id: int) -> int:
    """删除引导语导出的 Markdown 并从检索索引移除。"""
    cfg = config.load_config()
    root = Path(cfg.get("prompt_guides_dir", str(config.QR_HOME / "prompts"))).expanduser()
    removed = 0
    if not root.is_dir():
        return 0
    for path in root.glob(f"**/{guide_id:04d}-*.md"):
        _purge_document_path(conn, path)
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def purge_non_execute_prompts(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """按侧栏标题前缀清理引导语：仅保留「执行-」；时间线 events 不动。"""
    ensure_schema(conn)
    titles = cst.load_session_titles()
    frags = conn.execute(
        "SELECT id, event_uid, cursor_session_id, guide_id FROM prompt_guide_fragments",
    ).fetchall()
    bad_frag_ids: list[int] = []
    for f in frags:
        if not cst.should_include_in_prompt_guides(_fragment_session_title(f, titles)):
            bad_frag_ids.append(int(f["id"]))

    guides = conn.execute("SELECT id, origin FROM prompt_guides").fetchall()
    drop_guide_ids: list[int] = []
    kept_guide_ids: list[int] = []
    for g in guides:
        gid = int(g["id"])
        if g["origin"] == ORIGIN_MANUAL:
            kept_guide_ids.append(gid)
            continue
        gfrags = conn.execute(
            "SELECT cursor_session_id, event_uid FROM prompt_guide_fragments WHERE guide_id=?",
            (gid,),
        ).fetchall()
        if not gfrags:
            drop_guide_ids.append(gid)
            continue
        if any(
            cst.should_include_in_prompt_guides(_fragment_session_title(f, titles))
            for f in gfrags
        ):
            kept_guide_ids.append(gid)
        else:
            drop_guide_ids.append(gid)

    inbox_bad = sum(
        1 for f in frags
        if int(f["id"]) in bad_frag_ids and f["guide_id"] is None
    )
    stats: dict[str, Any] = {
        "dry_run": dry_run,
        "fragments_removed": len(bad_frag_ids),
        "inbox_removed": inbox_bad,
        "guides_removed": len(drop_guide_ids),
        "guides_kept": kept_guide_ids,
        "exports_removed": 0,
    }
    if dry_run:
        return stats

    exports = 0
    for gid in drop_guide_ids:
        exports += _remove_guide_exports(conn, gid)
        conn.execute("DELETE FROM prompt_guide_fragments WHERE guide_id=?", (gid,))
        conn.execute("DELETE FROM prompt_guides WHERE id=?", (gid,))

    if bad_frag_ids:
        ph = ",".join("?" * len(bad_frag_ids))
        conn.execute(
            f"DELETE FROM prompt_guide_fragments WHERE id IN ({ph})",
            bad_frag_ids,
        )

    stats["exports_removed"] = exports
    conn.commit()
    return stats


def recent_guide_projects(conn, start: int, end: int, *, limit: int = 4) -> list[str]:
    """近期合并/更新的引导语所属项目（用于规范修订优先级）。"""
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT project, MAX(updated_at) u FROM prompt_guides "
        "WHERE project IS NOT NULL AND trim(project)!='' "
        "AND updated_at>=? AND updated_at<=? "
        "GROUP BY project ORDER BY u DESC LIMIT ?",
        (start, end, limit * 2),
    ).fetchall()
    out: list[str] = []
    for r in rows:
        pid = workspace.normalize_project_id(str(r["project"] or ""))
        if pid and workspace.is_listable_project_id(pid) and pid not in out:
            out.append(pid)
        if len(out) >= limit:
            break
    return out


def stats(conn: sqlite3.Connection) -> dict:
    ensure_schema(conn)
    inbox = conn.execute(
        "SELECT COUNT(*) c FROM prompt_guide_fragments WHERE guide_id IS NULL",
    ).fetchone()["c"]
    guides = conn.execute("SELECT COUNT(*) c FROM prompt_guides").fetchone()["c"]
    by_origin = conn.execute(
        "SELECT origin, COUNT(*) c FROM prompt_guides GROUP BY origin",
    ).fetchall()
    return {
        "inbox": inbox,
        "guides": guides,
        "guides_by_origin": {r["origin"]: r["c"] for r in by_origin},
        "types": len(list_types(conn)),
    }


def _export_guide_markdown(
    guide_id: int,
    title: str,
    body: str,
    type_id: int | None,
    conn: sqlite3.Connection,
) -> Path | None:
    cfg = config.load_config()
    if not cfg.get("prompt_guides_export_md", True):
        return None
    root = Path(cfg.get("prompt_guides_dir", str(config.QR_HOME / "prompts"))).expanduser()
    slug = "general"
    if type_id:
        row = conn.execute(
            "SELECT slug FROM prompt_guide_types WHERE id=?", (type_id,),
        ).fetchone()
        if row:
            slug = row["slug"]
    dest_dir = root / slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", title)[:60].strip("-") or "guide"
    path = dest_dir / f"{guide_id:04d}-{safe}.md"
    header = f"# {title}\n\n> 引导语 #{guide_id} · 导出供检索与笔记同步\n\n"
    path.write_text(header + body + "\n", encoding="utf-8")
    return path
