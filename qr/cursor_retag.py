"""按问话内容将 Cursor 事件归到正确 workspace 项目。"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from . import cursor_archive, timeline_search
from . import workspace

TARGET_SPORTS = workspace.TARGET_SPORTS
TARGET_QR = workspace.TARGET_QR

# 用户指定的整段会话迁移（标题 → session_id）
KNOWN_SPORTS_SESSIONS: dict[str, str] = {
    "487058d0-18ce-4eb6-8f6b-3487961d7ff9": "中考体育考试系统开发",
    "c17b916a-8520-428f-983d-255d20422144": "全国体考规则资料查询",
    "f170ddbe-f4c6-429d-8671-eb61c0238809": "全国体考规则分类分析",
}

_SPORTS = re.compile(
    r"中考体育|体考规则|体考资料|project[- ]?sports|恒鸿达|"
    r"赵县|灵寿|体育考试系统|立项.*体育|体育项目|"
    r"exam-rules|hunan-sports|DEVICE_REQUIREMENTS|"
    r"搜集.*(省市|全国).*(体育|体考)|体考.*搜集|"
    r"分类分析.*体考|考试项目.*占比|"
    r"8000个考生|400米跑道|"
    r"帮我在project sports|完全根据你的建议，现在立项。项目名称是project-sports|"
    r"如果开发那个体育项目",
    re.I,
)
_QR_OPS = re.compile(
    r"今日总览|全量自检|侧栏|nav-sub|运维|ingest\.cursor|"
    r"workspace migrate|两个项目分开|知识库怎么进入|cursor 事件|"
    r"采集数据里出现|_clean_project|Failed to fetch|qr doctor|"
    r"引导语收件箱|接着干|README 功能点|移动端.*远程|"
    r"开 workspace.*project-sports|project-sports.*开 workspace|"
    r"下一关：在 Cursor|验证 QR cursor 采集",
    re.I,
)
# 整段会话摘要归属（体育业务为主）
_SPORTS_SESSION_IDS = frozenset({
    "487058d0-18ce-4eb6-8f6b-3487961d7ff9",
    "c17b916a-8520-428f-983d-255d20422144",
    "f170ddbe-f4c6-429d-8671-eb61c0238809",
    "a56e7d22-a75f-4875-81f8-bfdfdbb1880b",
    "6e7d644e-2f8d-4f3f-95ac-f95eb6b7404b",
})


def is_sports_turn(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) < 6:
        return False
    if _QR_OPS.search(t):
        return False
    if _SPORTS.search(t):
        return True
    if re.search(r"立项体育|体育项目.*QR|QR.*体育项目", t, re.I):
        return True
    return False


def _update_note_summary(
    conn: sqlite3.Connection,
    session_id: str,
    target: str,
) -> bool:
    note_uid = f"note:cursor-summary:{session_id}"
    row = conn.execute(
        "SELECT uid, project, content, title FROM events WHERE uid=?",
        (note_uid,),
    ).fetchone()
    if not row or row["project"] == target:
        return False
    conn.execute(
        "UPDATE events SET project=?, title=? WHERE uid=?",
        (target, f"Cursor 会话摘要 · {target}", note_uid),
    )
    path = Path(row["content"] or "")
    if path.is_file():
        try:
            body = path.read_text(encoding="utf-8")
            body = re.sub(
                r"^# Cursor 会话摘要 · \S+",
                f"# Cursor 会话摘要 · {target}",
                body,
                count=1,
            )
            path.write_text(body, encoding="utf-8")
        except OSError:
            pass
    timeline_search.index_event(
        conn,
        uid=note_uid,
        source="note",
        project=target,
        title=f"Cursor 会话摘要 · {target}",
        content=path.read_text(encoding="utf-8") if path.is_file() else "",
    )
    return True


def migrate_cursor_sessions(
    conn: sqlite3.Connection,
    session_ids: list[str],
    *,
    target: str = TARGET_SPORTS,
    dry_run: bool = False,
) -> dict[str, int]:
    """将整段 Cursor 会话（全部问话）迁到指定 project。"""
    stats = {"sessions": 0, "events": 0, "fragments": 0, "notes": 0, "archives": 0}
    for sid in session_ids:
        sid = sid.strip()
        if not sid:
            continue
        pattern = f"cursor:{sid}:q%"
        rows = conn.execute(
            "SELECT uid, project, title, content FROM events "
            "WHERE source='cursor' AND uid LIKE ?",
            (pattern,),
        ).fetchall()
        if not rows:
            continue
        stats["sessions"] += 1
        for row in rows:
            if row["project"] == target:
                continue
            stats["events"] += 1
            if dry_run:
                continue
            conn.execute(
                "UPDATE events SET project=? WHERE uid=?",
                (target, row["uid"]),
            )
            cur = conn.execute(
                "UPDATE prompt_guide_fragments SET project=? WHERE event_uid=?",
                (target, row["uid"]),
            )
            stats["fragments"] += cur.rowcount
            timeline_search.index_event(
                conn,
                uid=row["uid"],
                source="cursor",
                project=target,
                title=row["title"] or "",
                content=row["content"] or "",
            )
        if not dry_run:
            if _update_note_summary(conn, sid, target):
                stats["notes"] += 1
            meta = cursor_archive.archive_root() / sid / "meta.json"
            if meta.is_file():
                try:
                    data = json.loads(meta.read_text(encoding="utf-8"))
                    if data.get("project") != target:
                        data["project"] = target
                        meta.write_text(
                            json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        stats["archives"] += 1
                except (OSError, json.JSONDecodeError):
                    pass
    if not dry_run:
        conn.commit()
    return stats


def preview_session_migrate(
    conn: sqlite3.Connection,
    session_ids: list[str],
    *,
    target: str = TARGET_SPORTS,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for sid in session_ids:
        sid = sid.strip()
        label = KNOWN_SPORTS_SESSIONS.get(sid, sid[:8])
        rows = conn.execute(
            "SELECT project FROM events WHERE source='cursor' AND uid LIKE ?",
            (f"cursor:{sid}:q%",),
        ).fetchall()
        if not rows:
            out.append({"session": sid, "label": label, "turns": "0", "move": "0"})
            continue
        move = sum(1 for r in rows if r["project"] != target)
        out.append({
            "session": sid,
            "label": label,
            "turns": str(len(rows)),
            "move": str(move),
        })
    return out


def apply_sports_retag(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """体育相关 Cursor 问话 → dev/sports/project-sports；误标 sports → dev/qr。"""
    stats = {
        "to_sports": 0,
        "to_qr": 0,
        "fragments": 0,
        "notes": 0,
        "archives": 0,
    }
    rows = conn.execute(
        "SELECT uid, project, title FROM events WHERE source='cursor'",
    ).fetchall()

    for row in rows:
        uid, project, title = row["uid"], row["project"], row["title"] or ""
        if is_sports_turn(title):
            if project != TARGET_SPORTS:
                stats["to_sports"] += 1
                if not dry_run:
                    conn.execute(
                        "UPDATE events SET project=? WHERE uid=?",
                        (TARGET_SPORTS, uid),
                    )
        elif project == "sports":
            stats["to_qr"] += 1
            if not dry_run:
                conn.execute(
                    "UPDATE events SET project=? WHERE uid=?",
                    (TARGET_QR, uid),
                )

    if not dry_run:
        for row in rows:
            uid = row["uid"]
            new_proj = None
            if is_sports_turn(row["title"] or ""):
                new_proj = TARGET_SPORTS
            elif row["project"] == "sports":
                new_proj = TARGET_QR
            if new_proj:
                cur = conn.execute(
                    "UPDATE prompt_guide_fragments SET project=? "
                    "WHERE event_uid=? AND project IS NOT ?",
                    (new_proj, uid, new_proj),
                )
                stats["fragments"] += cur.rowcount

        for row in conn.execute(
            "SELECT uid, project, content, title FROM events "
            "WHERE source='note' AND uid LIKE 'note:cursor-summary:%'",
        ):
            sid = row["uid"].removeprefix("note:cursor-summary:")
            target = (
                TARGET_SPORTS if sid in _SPORTS_SESSION_IDS else TARGET_QR
            )
            if row["project"] == target:
                continue
            if row["project"] not in ("sports", "qr", None, TARGET_QR):
                continue
            if sid not in _SPORTS_SESSION_IDS and row["project"] in (
                TARGET_SPORTS,
                None,
            ):
                continue
            stats["notes"] += 1
            conn.execute(
                "UPDATE events SET project=?, title=? WHERE uid=?",
                (
                    target,
                    f"Cursor 会话摘要 · {target}",
                    row["uid"],
                ),
            )
            path = Path(row["content"] or "")
            if path.is_file():
                try:
                    body = path.read_text(encoding="utf-8")
                    old = row["title"] or ""
                    if old.startswith("Cursor 会话摘要"):
                        body = body.replace(
                            old.replace("Cursor 会话摘要 · ", "# Cursor 会话摘要 · "),
                            f"# Cursor 会话摘要 · {target}",
                            1,
                        )
                    elif body.startswith("# Cursor 会话摘要"):
                        body = re.sub(
                            r"^# Cursor 会话摘要 · \S+",
                            f"# Cursor 会话摘要 · {target}",
                            body,
                            count=1,
                        )
                    path.write_text(body, encoding="utf-8")
                except OSError:
                    pass

        for sid in _SPORTS_SESSION_IDS:
            meta = cursor_archive.archive_root() / sid / "meta.json"
            if not meta.is_file():
                continue
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("project") == TARGET_SPORTS:
                continue
            stats["archives"] += 1
            data["project"] = TARGET_SPORTS
            meta.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        conn.commit()
    return stats


def preview_sports_retag(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """预览将调整的问话标题（供 CLI / 测试）。"""
    out: dict[str, list[str]] = {"to_sports": [], "to_qr": []}
    for row in conn.execute(
        "SELECT uid, project, title FROM events WHERE source='cursor'",
    ):
        title = (row["title"] or "")[:100]
        if is_sports_turn(row["title"] or ""):
            if row["project"] != TARGET_SPORTS:
                out["to_sports"].append(f"[{row['project']}] {title}")
        elif row["project"] == "sports":
            out["to_qr"].append(f"[{row['project']}] {title}")
    return out
