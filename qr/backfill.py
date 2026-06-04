from __future__ import annotations

import sqlite3
import time

from . import config
from .collectors import COLLECTORS

BACKFILL_SOURCES = ["shell", "git", "files", "cursor", "notes"]


def since_ts(days: int) -> int:
    return int(time.time()) - days * 86400


def run(
    conn: sqlite3.Connection,
    days: int | None = None,
    sources: list[str] | None = None,
) -> dict[str, int | str]:
    cfg = config.load_config()
    days = int(days if days is not None else cfg.get("backfill_days", 365))
    days = max(1, days)
    since = since_ts(days)
    names = [s for s in (sources or BACKFILL_SOURCES) if s in COLLECTORS]
    roots = config.scan_roots(cfg)
    result: dict[str, int | str] = {
        "days": days,
        "since": time.strftime("%Y-%m-%d", time.localtime(since)),
    }
    for name in names:
        fn = COLLECTORS[name]
        result[name] = fn(conn, backfill=True, since_ts=since, roots=roots)
    return result
