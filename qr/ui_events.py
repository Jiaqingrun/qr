"""Web UI 行为事件（本地 jsonl，供后续分析）。"""
from __future__ import annotations

import json
from typing import Any

from . import config, db


def log(event: str, **fields: Any) -> None:
    config.ensure_dirs()
    path = config.LOGS_DIR / "ui_events.jsonl"
    row = {"ts": db.now(), "event": event, **fields}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
