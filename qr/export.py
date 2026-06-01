from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import config, db


def export_obsidian(dest: Path | None = None) -> Path:
    dest = (dest or Path.home() / "Documents" / "QR-Export").expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    notes = dest / "notes"
    sums = dest / "summaries"
    chats = dest / "chats"
    for d in (notes, sums, chats):
        d.mkdir(exist_ok=True)

    with db.session() as conn:
        ev = conn.execute(
            "SELECT ts, source, project, title, content FROM events WHERE source='note' ORDER BY ts DESC"
        ).fetchall()
        for r in ev:
            day = time.strftime("%Y%m%d", time.localtime(r["ts"]))
            fn = notes / f"{day}-{hash(r['content']) % 100000}.md"
            fn.write_text(
                f"---\ntags: [qr, note]\nproject: {r['project'] or ''}\n---\n\n"
                f"# {r['title']}\n\n{r['content']}\n",
                encoding="utf-8",
            )
        rows = conn.execute(
            "SELECT id, period, start_ts, end_ts, content FROM summaries ORDER BY end_ts DESC"
        ).fetchall()
        for r in rows:
            fn = sums / f"summary-{r['id']}-{r['period']}.md"
            fn.write_text(r["content"] or "", encoding="utf-8")
        sessions = conn.execute(
            "SELECT id, title, updated_at FROM chat_sessions ORDER BY updated_at DESC LIMIT 100"
        ).fetchall()
        for s in sessions:
            msgs = conn.execute(
                "SELECT role, content FROM chat_messages WHERE session_id=? ORDER BY id",
                (s["id"],),
            ).fetchall()
            body = "\n\n".join(f"## {m['role']}\n\n{m['content']}" for m in msgs)
            fn = chats / f"chat-{s['id']}.md"
            fn.write_text(f"# {s['title']}\n\n{body}\n", encoding="utf-8")

    if config.STANDARDS_PATH.exists():
        shutil.copy2(config.STANDARDS_PATH, dest / "standards.md")
    index = dest / "README.md"
    index.write_text(
        "# QR 知识库 Obsidian 导出\n\n"
        f"- notes/ 笔记\n- summaries/ 周期总结\n- chats/ 问答历史\n- standards.md 个人规范\n",
        encoding="utf-8",
    )
    return dest
