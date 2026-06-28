"""M3-3 Cursor 长会话 checkpoint（已完成 / 待办 / 风险）。"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

from . import config, cursor_archive, db, timeline_search

_CURSOR_UID_RE = re.compile(r"^cursor:([^:]+):q(\d+)$")
_CHECKPOINT_SYSTEM = (
    "你是个人项目复盘助手。根据 Cursor 对话摘录，输出结构化 checkpoint Markdown。"
    "必须包含且仅包含三个二级标题：## 已完成、## 待办、## 风险。"
    "每条用 - 列表；无内容写「（暂无）」。不要输出其它章节或解释。"
)


def min_turns(cfg: dict[str, Any] | None = None) -> int:
    cfg = cfg or config.load_config()
    return max(1, int(cfg.get("session_checkpoint_min_turns", 40)))


def parse_cursor_event_uid(uid: str | None) -> tuple[str, int] | None:
    m = _CURSOR_UID_RE.match((uid or "").strip())
    if not m:
        return None
    return m.group(1), int(m.group(2))


def checkpoint_uid(session_id: str) -> str:
    h = hashlib.sha1(session_id.encode("utf-8", "replace")).hexdigest()[:12]
    return f"note:checkpoint:{h}"


def turn_counts(conn: sqlite3.Connection, session_ids: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for sid in session_ids:
        if not sid:
            continue
        row = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE source='cursor' AND uid LIKE ?",
            (f"cursor:{sid}:q%",),
        ).fetchone()
        out[sid] = int(row["c"]) if row else 0
    return out


def existing_checkpoints(
    conn: sqlite3.Connection, session_ids: list[str]
) -> dict[str, str]:
    out: dict[str, str] = {}
    for sid in session_ids:
        uid = checkpoint_uid(sid)
        row = conn.execute(
            "SELECT uid FROM events WHERE uid=?", (uid,)
        ).fetchone()
        if row:
            out[sid] = uid
    return out


def load_turns(conn: sqlite3.Connection, session_id: str) -> tuple[list[dict], str]:
    """从 events 或归档加载轮次。返回 (turns, project)。"""
    rows = conn.execute(
        "SELECT uid, title, content, meta, ts FROM events WHERE source='cursor' "
        "AND uid LIKE ? ORDER BY ts ASC, uid ASC",
        (f"cursor:{session_id}:q%",),
    ).fetchall()
    project = ""
    turns: list[dict] = []
    for row in rows:
        parsed = parse_cursor_event_uid(row["uid"])
        qidx = parsed[1] if parsed else len(turns)
        meta_raw = row["meta"] or ""
        if meta_raw:
            try:
                mobj = json.loads(meta_raw)
                project = str(mobj.get("project") or project or "")
            except json.JSONDecodeError:
                pass
        body = cursor_archive.read_turn(session_id, qidx)
        turns.append({
            "query": (body or {}).get("query") or row["title"] or "",
            "reply": (body or {}).get("reply") or "",
            "ts": int(row["ts"]),
            "query_index": qidx,
        })
    if turns:
        return turns, project
    return load_turns_archive_only(session_id)


def load_turns_archive_only(session_id: str) -> tuple[list[dict], str]:
    project = ""
    meta_file = cursor_archive.archive_root() / session_id / "meta.json"
    if meta_file.is_file():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            project = str(meta.get("project") or "")
        except (OSError, json.JSONDecodeError):
            pass
    turns: list[dict] = []
    root = cursor_archive.archive_root() / session_id
    for md in sorted(root.glob("q*.md")):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        parts = text.split("\n---\n", 1)
        try:
            qidx = int(md.stem[1:])
        except ValueError:
            qidx = len(turns)
        turns.append({
            "query": parts[0].strip(),
            "reply": parts[1].strip() if len(parts) > 1 else "",
            "ts": int(md.stat().st_mtime),
            "query_index": qidx,
        })
    return turns, project


def get_checkpoint(conn: sqlite3.Connection, session_id: str) -> dict | None:
    uid = checkpoint_uid(session_id)
    row = conn.execute(
        "SELECT uid, title, content, ts, meta, project FROM events WHERE uid=?",
        (uid,),
    ).fetchone()
    if not row:
        return None
    body = row["content"] or ""
    if body.startswith("/") or body.startswith("~"):
        try:
            from pathlib import Path

            p = Path(body).expanduser()
            if p.is_file():
                body = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return {
        "uid": row["uid"],
        "title": row["title"],
        "content": body,
        "ts": int(row["ts"]),
        "project": row["project"],
        "meta": row["meta"],
    }


def _extractive_body(session_id: str, project: str, turns: list[dict]) -> str:
    done: list[str] = []
    todo: list[str] = []
    risks: list[str] = []
    for t in turns[-15:]:
        q = (t.get("query") or "").strip()
        if not q:
            continue
        line = q.splitlines()[0][:160]
        low = line.lower()
        if any(k in line for k in ("完成", "已修", "已加", "落地", "合并")):
            done.append(line)
        elif any(k in line for k in ("待", "TODO", "下一步", "继续", "还没")):
            todo.append(line)
        elif any(k in line for k in ("风险", "问题", "失败", "阻塞", "bug")):
            risks.append(line)
        elif "?" in line or "？" in line:
            todo.append(line)
    if not done:
        done = [
            (t.get("query") or "").splitlines()[0][:120]
            for t in turns[:8]
            if (t.get("query") or "").strip()
        ][:6]
    if not todo:
        todo = ["（从末轮摘录整理）"]
    if not risks:
        risks = ["（暂无显式风险记录）"]
    return _format_checkpoint(session_id, project, len(turns), done, todo, risks)


def _format_checkpoint(
    session_id: str,
    project: str,
    turn_count: int,
    done: list[str],
    todo: list[str],
    risks: list[str],
) -> str:
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {x}" for x in items[:12]) or "- （暂无）"

    return (
        f"# 会话 Checkpoint\n\n"
        f"session: `{session_id}`\n"
        f"项目: {project or '（未标注）'}\n"
        f"轮次: {turn_count}\n\n"
        f"## 已完成\n\n{bullets(done)}\n\n"
        f"## 待办\n\n{bullets(todo)}\n\n"
        f"## 风险\n\n{bullets(risks)}\n"
    )


def _llm_body(session_id: str, project: str, turns: list[dict], cfg: dict) -> str:
    from .ollama_client import Ollama, OllamaError

    sample: list[str] = []
    head = turns[:12]
    tail = turns[-8:] if len(turns) > 20 else []
    seen: set[int] = set()
    for t in [*head, *tail]:
        qi = int(t.get("query_index", 0))
        if qi in seen:
            continue
        seen.add(qi)
        q = (t.get("query") or "").strip().splitlines()[0][:200]
        r = (t.get("reply") or "").strip()[:400]
        sample.append(f"Q: {q}\nA: {r[:400]}")
    prompt = (
        f"项目: {project or '未知'}\n"
        f"会话 ID: {session_id}\n"
        f"总轮次: {len(turns)}\n\n"
        "对话摘录:\n"
        + "\n---\n".join(sample[:18])
        + "\n\n请输出 checkpoint Markdown（## 已完成 / ## 待办 / ## 风险）。"
    )
    text = Ollama().generate(
        prompt,
        system=_CHECKPOINT_SYSTEM,
        strip_think=True,
        timeout=float(cfg.get("session_checkpoint_timeout_seconds", 300)),
    )
    if "## 已完成" not in text or "## 待办" not in text:
        raise ValueError("模型输出缺少必需章节")
    header = (
        f"# 会话 Checkpoint\n\n"
        f"session: `{session_id}`\n"
        f"项目: {project or '（未标注）'}\n"
        f"轮次: {len(turns)}\n\n"
    )
    if text.lstrip().startswith("#"):
        return text.strip() + "\n"
    return header + text.strip() + "\n"


def generate_body(
    session_id: str,
    project: str,
    turns: list[dict],
    *,
    cfg: dict | None = None,
) -> str:
    cfg = cfg or config.load_config()
    if cfg.get("session_checkpoint_use_llm", True) and len(turns) >= 3:
        try:
            return _llm_body(session_id, project, turns, cfg)
        except Exception:
            pass
    return _extractive_body(session_id, project, turns)


def create_checkpoint(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    force: bool = False,
    project: str | None = None,
) -> dict[str, Any]:
    """生成长会话 checkpoint 并写入时间线。"""
    sid = (session_id or "").strip()
    if not sid:
        raise ValueError("session_id 不能为空")
    cfg = config.load_config()
    turns, proj = load_turns(conn, sid)
    if not turns:
        turns, proj = load_turns_archive_only(sid)
    if len(turns) < min_turns(cfg):
        raise ValueError(
            f"会话仅 {len(turns)} 轮，未达到 checkpoint 阈值 {min_turns(cfg)}"
        )
    proj = project or proj or ""
    if not force:
        existing = get_checkpoint(conn, sid)
        if existing:
            return {**existing, "created": False}

    body = generate_body(sid, proj, turns, cfg=cfg)
    note_path = config.QR_HOME / "notes" / f"cursor-checkpoint-{sid[:8]}.md"
    config.ensure_dirs()
    note_path.write_text(body, encoding="utf-8")

    from . import workspace

    pid = workspace.canonical_project_id(proj) if proj else None
    uid = checkpoint_uid(sid)
    ts = max((int(t.get("ts", 0)) for t in turns), default=db.now())
    meta = json.dumps(
        {"kind": "checkpoint", "session_id": sid, "turn_count": len(turns)},
        ensure_ascii=False,
    )
    title = f"[Checkpoint] 会话 {sid[:8]}… · {len(turns)} 轮"
    db.upsert_event(
        conn,
        uid=uid,
        ts=ts,
        source="note",
        project=pid,
        title=title,
        content=body,
        meta=meta,
    )
    timeline_search.index_event(
        conn,
        uid=uid,
        source="note",
        project=pid,
        title=title,
        content=body,
    )
    return {
        "uid": uid,
        "title": title,
        "content": body,
        "ts": ts,
        "project": pid,
        "session_id": sid,
        "turn_count": len(turns),
        "created": True,
        "note_path": str(note_path),
    }


def enrich_timeline_items(
    conn: sqlite3.Connection,
    items: list[dict[str, Any]],
    *,
    cfg: dict | None = None,
) -> None:
    """为时间线 cursor 条目附加会话轮次与 checkpoint 按钮标记。"""
    cfg = cfg or config.load_config()
    threshold = min_turns(cfg)
    session_ids: list[str] = []
    for item in items:
        if item.get("source") != "cursor":
            continue
        parsed = parse_cursor_event_uid(item.get("uid"))
        if not parsed:
            continue
        sid, qidx = parsed
        item["session_id"] = sid
        item["query_index"] = qidx
        session_ids.append(sid)
    if not session_ids:
        return
    unique = list(dict.fromkeys(session_ids))
    counts = turn_counts(conn, unique)
    cps = existing_checkpoints(conn, unique)
    max_q: dict[str, int] = {}
    for item in items:
        sid = item.get("session_id")
        if not sid:
            continue
        qidx = int(item.get("query_index") or 0)
        max_q[sid] = max(max_q.get(sid, -1), qidx)
    for item in items:
        sid = item.get("session_id")
        if not sid:
            continue
        n = counts.get(sid, 0)
        item["session_turns"] = n
        item["session_long"] = n >= threshold
        cp_uid = cps.get(sid)
        if cp_uid:
            item["checkpoint_uid"] = cp_uid
            item["show_checkpoint_btn"] = False
        elif n >= threshold and int(item.get("query_index") or 0) == max_q.get(sid, -1):
            item["show_checkpoint_btn"] = True
        else:
            item["show_checkpoint_btn"] = False


def list_long_sessions(
    conn: sqlite3.Connection,
    *,
    min_count: int | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    threshold = min_count if min_count is not None else min_turns()
    rows = conn.execute(
        "SELECT uid, ts, project, title FROM events WHERE source='cursor' "
        "AND uid LIKE 'cursor:%:q%'",
    ).fetchall()
    by_session: dict[str, dict[str, Any]] = {}
    for row in rows:
        parsed = parse_cursor_event_uid(row["uid"])
        if not parsed:
            continue
        sid, _ = parsed
        ent = by_session.setdefault(
            sid,
            {
                "session_id": sid,
                "turns": 0,
                "last_ts": 0,
                "project": row["project"],
                "last_title": row["title"],
            },
        )
        ent["turns"] += 1
        ent["last_ts"] = max(ent["last_ts"], int(row["ts"]))
        if row["project"]:
            ent["project"] = row["project"]
        if row["title"]:
            ent["last_title"] = row["title"]
    out = [s for s in by_session.values() if s["turns"] >= threshold]
    out.sort(key=lambda x: x["last_ts"], reverse=True)
    cps = existing_checkpoints(conn, [s["session_id"] for s in out])
    for s in out:
        s["has_checkpoint"] = s["session_id"] in cps
        s["checkpoint_uid"] = cps.get(s["session_id"])
    return out[:limit]
