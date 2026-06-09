from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from . import config, db, indexer
from .ollama_client import Ollama

_TAG_RE = re.compile(r"<[^>]+>")


def _clean_project(name: str) -> str:
    from . import workspace

    mapped = workspace.project_from_cursor_dir_name(name)
    if mapped:
        return mapped
    parts = name.split("-")
    return parts[-1] if parts else name


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


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


def _format_transcript(path: Path, project: str) -> str:
    lines = [
        f"# Cursor 对话全文 · 项目 {project}",
        f"路径: {path}",
        "",
    ]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            turn = 0
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    d = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                role = d.get("role", "")
                msg = d.get("message", {})
                content = msg.get("content") if isinstance(msg, dict) else None
                texts: list[str] = []
                if isinstance(content, str):
                    texts = [_strip_tags(content)]
                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            t = item.get("text")
                            if t:
                                texts.append(_strip_tags(t))
                for t in texts:
                    if not t or t.startswith("[{"):
                        continue
                    turn += 1
                    label = "用户" if role == "user" else "助手"
                    lines.append(f"## [{label} #{turn}]")
                    lines.append(t[:4000])
                    lines.append("")
    except OSError:
        return ""
    return "\n".join(lines).strip()


def index_transcripts(reindex: bool = False) -> dict[str, int]:
    cfg = config.load_config()
    base = Path(os.path.expanduser(cfg["cursor_projects_dir"]))
    if not base.exists():
        return {"files": 0, "chunks": 0, "skipped": 0}

    stats = {"files": 0, "chunks": 0, "skipped": 0}
    ol = Ollama()
    with db.session() as conn:
        for uuid, jsonl in _iter_transcripts(base).items():
            project = _clean_project(jsonl.parts[len(base.parts)])
            text = _format_transcript(jsonl, project)
            if not text:
                continue
            indexer._index_document(
                conn, jsonl, f"cursor-{project}", text, cfg, ol, reindex, stats,
            )
    return stats
