from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from .. import config, cursor_archive, db, timeutil

_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_PASTE_MARKERS = (
    re.compile(r"\n仅提问\s*·"),
    re.compile(r"\n查看对话\s*\n"),
    re.compile(r"\n提问\s*\n"),
    re.compile(r"\n回复\s*\n"),
)
_TRAILING_UI_LINE = re.compile(
    r"^(cursor|查看对话|file)$|并且约\s+.+\s+估\s*$",
    re.MULTILINE,
)


def _texts(message) -> list[str]:
    out: list[str] = []
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                t = item.get("text")
                if t:
                    out.append(t)
    return out


def _resolve_project(slug: str, cfg: dict) -> tuple[str, dict]:
    from .. import workspace

    pid, needs_review = workspace.resolve_cursor_project(slug, cfg)
    meta: dict = {"cursor_slug": slug}
    if needs_review:
        meta["needs_review"] = True
        return "", meta
    return pid or "", meta


def _extract_query(text: str) -> str | None:
    m = _QUERY_RE.search(text)
    q = m.group(1).strip() if m else _TAG_RE.sub("", text).strip()
    if not q or q.startswith("[{"):
        return None
    return sanitize_user_query(q)


def sanitize_user_query(q: str) -> str:
    """去掉用户消息里粘贴的时间线/旧归档片段，保留真实提问。"""
    text = (q or "").strip()
    if not text:
        return ""
    for pat in _PASTE_MARKERS:
        m = pat.search(text)
        if m:
            text = text[: m.start()].rstrip()
    text = _TRAILING_UI_LINE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _parse_transcript(path: Path) -> list[dict]:
    queries: list[dict] = []
    assistant_turns = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                role = d.get("role")
                msg = d.get("message", {})
                texts = _texts(msg)
                if role == "user":
                    for t in texts:
                        q = _extract_query(t)
                        if not q:
                            continue
                        queries.append({
                            "query": q,
                            "ts": timeutil.parse_cursor_timestamp(t),
                            "assistant_before": assistant_turns,
                        })
                elif role == "assistant":
                    assistant_turns += 1
    except OSError:
        return []
    return queries


def _iter_transcripts(base: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for jsonl in base.glob("*/agent-transcripts/*/*.jsonl"):
        uuid = jsonl.stem
        try:
            mt = jsonl.stat().st_mtime
        except OSError:
            continue
        prev = found.get(uuid)
        if prev is None or mt > prev.stat().st_mtime:
            found[uuid] = jsonl
    return found


def _assign_query_times(queries: list[dict], jsonl: Path) -> None:
    """为无 <timestamp> 的消息插值；活跃会话以转录 mtime 为末段锚点，避免旧标签把新问话压在几天前。"""
    if not queries:
        return
    file_start, file_end = timeutil.file_time_bounds(jsonl)
    known = {i: int(q["ts"]) for i, q in enumerate(queries) if q.get("ts")}
    if known and file_end > max(known.values()) + 3600:
        # 转录仍在更新，但消息里嵌的是较早的 <timestamp>：只保留近 24h 内的锚点
        known = {i: t for i, t in known.items() if t >= file_end - 86400}
    if known:
        start_ts = min(min(known.values()), file_start)
        end_ts = max(max(known.values()), file_end)
    else:
        start_ts, end_ts = file_start, file_end
    times = timeutil.interpolate_series(
        len(queries), known, start_ts=start_ts, end_ts=end_ts, step_seconds=45,
    )
    for i, q in enumerate(queries):
        q["ts"] = times[i]
        q["ts_estimated"] = i not in known


def _max_event_ts(conn: sqlite3.Connection, uuid: str) -> int:
    row = conn.execute(
        "SELECT MAX(ts) t FROM events WHERE uid LIKE ?",
        (f"cursor:{uuid}:q%",),
    ).fetchone()
    return int(row["t"] or 0)


def _clear_cursor_state(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM events WHERE source='cursor'")


def _slug(q: str) -> str:
    return hashlib.sha1(q.encode("utf-8", "replace")).hexdigest()[:10]


def _upsert_query_event(
    conn: sqlite3.Connection,
    *,
    uuid: str,
    idx: int,
    total: int,
    query: str,
    reply: str,
    ts: int,
    project: str,
    ts_estimated: bool = False,
    chat_title: str = "",
    cfg: dict | None = None,
    meta_extra: dict | None = None,
) -> None:
    from .. import cursor_session_title as cst

    uid = f"cursor:{uuid}:q{idx}"
    archive = cursor_archive.turn_path(uuid, idx)
    title = (query.splitlines()[0].strip() if query else "")[:120] or cursor_archive.turn_filename(idx)
    content = str(archive.resolve())
    meta_obj: dict = {
        "ts_estimated": bool(ts_estimated),
        "session_id": uuid,
        "query_index": idx,
        "archive_path": cursor_archive.turn_relpath(uuid, idx),
        "has_reply": bool(reply.strip()),
    }
    if meta_extra:
        meta_obj.update(meta_extra)
    from .. import sensitive_scan

    meta_obj.update(sensitive_scan.meta_patch_for_content(query, reply))
    meta_obj.update(cst.prefix_meta_for_chat(chat_title, project, cfg=cfg))
    meta = json.dumps(meta_obj, ensure_ascii=False)
    db.upsert_event(
        conn,
        uid=uid,
        ts=ts,
        source="cursor",
        project=project,
        title=title,
        content=content,
        meta=meta,
    )


def collect(
    conn: sqlite3.Connection,
    *,
    backfill: bool = False,
    since_ts: int | None = None,
    roots=None,
) -> int:
    cfg = config.load_config()
    base = Path(cfg["cursor_projects_dir"]).expanduser()
    if not base.exists():
        return 0

    from .. import prompt_guides as pg
    from .. import workspace

    workspace.sync_cursor_roots_registry(cfg, persist=True)

    if backfill:
        _clear_cursor_state(conn)

    titles = {}
    try:
        from .. import cursor_session_title as cst

        titles = cst.load_session_titles(cfg=cfg)
    except Exception:
        titles = {}

    new = 0
    for uuid, jsonl in _iter_transcripts(base).items():
        try:
            data = jsonl.read_bytes()
        except OSError:
            continue

        sig = hashlib.sha256(data).hexdigest()
        state_key = f"cursor_sig:{uuid}"

        if pg.is_session_dismissed(conn, uuid):
            db.set_state(conn, state_key, sig)
            continue

        turns = cursor_archive.parse_transcript_turns(jsonl)
        max_q_ts = max((int(t["ts"]) for t in turns), default=0)
        cursor_slug = jsonl.parts[len(base.parts)]
        project, slug_meta = _resolve_project(cursor_slug, cfg)
        meta_file = cursor_archive.archive_root() / uuid / "meta.json"
        archive_ver = 0
        if meta_file.is_file():
            try:
                archive_ver = int(
                    json.loads(meta_file.read_text(encoding="utf-8")).get(
                        "archive_version", 0
                    )
                )
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                archive_ver = 0
        needs_archive = bool(turns) and (
            archive_ver < cursor_archive.ARCHIVE_VERSION
            or not meta_file.exists()
            or meta_file.stat().st_mtime < jsonl.stat().st_mtime
        )
        if needs_archive:
            cursor_archive.archive_session(uuid, jsonl, project=project or cursor_slug)
        stale_ts = (
            not backfill
            and not needs_archive
            and db.get_state(conn, state_key) == sig
            and max_q_ts <= _max_event_ts(conn, uuid) + 90
        )
        if stale_ts:
            continue

        conn.execute("DELETE FROM events WHERE uid=?", (f"cursor:{uuid}",))

        if not turns:
            db.set_state(conn, state_key, sig)
            continue

        for t in turns:
            i = int(t["query_index"])
            ts = int(t["ts"])
            if since_ts and ts < since_ts:
                continue
            event_uid = f"cursor:{uuid}:q{i}"
            if pg._is_dismissed(conn, event_uid):
                continue
            _upsert_query_event(
                conn,
                uuid=uuid,
                idx=i,
                total=len(turns),
                query=t["query"],
                reply=t.get("reply") or "",
                ts=ts,
                project=project,
                ts_estimated=bool(t.get("ts_estimated")),
                chat_title=titles.get(uuid, ""),
                cfg=cfg,
                meta_extra=slug_meta,
            )
            new += 1

        stale = conn.execute(
            "SELECT uid FROM events WHERE source='cursor' AND uid LIKE ?",
            (f"cursor:{uuid}:q%",),
        ).fetchall()
        for row in stale:
            try:
                idx = int(row["uid"].rsplit(":q", 1)[-1])
            except ValueError:
                continue
            if idx >= len(turns):
                conn.execute("DELETE FROM events WHERE uid=?", (row["uid"],))

        db.set_state(conn, state_key, sig)
        from .. import session_summary

        session_summary.maybe_summarize_session(
            conn, uuid, project=project, turns=turns,
        )

    try:
        from .. import cursor_session_title as cst

        cst.refresh_prefix_annotations(conn, cfg=cfg)
    except Exception:
        pass

    return new
