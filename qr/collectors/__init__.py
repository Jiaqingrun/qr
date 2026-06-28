from __future__ import annotations

from .. import config, db
from . import cursor, files, gitlog, notes, shell

COLLECTORS = {
    "shell": shell.collect,
    "git": gitlog.collect,
    "files": files.collect,
    "cursor": cursor.collect,
    "notes": notes.collect,
}


def run(
    conn,
    sources: list[str],
    *,
    backfill: bool = False,
    since_ts: int | None = None,
    roots=None,
) -> dict[str, int]:
    result: dict[str, int] = {}
    for name in sources:
        fn = COLLECTORS.get(name)
        if fn is None:
            continue
        if backfill:
            result[name] = fn(conn, backfill=True, since_ts=since_ts, roots=roots)
        else:
            result[name] = fn(conn)
    if "cursor" in sources:
        from .. import prompt_guides

        pg = prompt_guides.sync_cursor_inbox(conn)
        result["prompt_fragments"] = pg.get("new", 0)
    if not backfill:
        from .. import proactive

        alerts = proactive.collect_all(conn)
        if alerts:
            proactive.persist_digest(alerts)
            result["alerts"] = len(alerts)
        db.set_state(conn, "ingest_last_ts", str(db.now()))
        cfg = config.load_config()
        if cfg.get("index_incremental_after_ingest", True):
            try:
                from .. import indexer

                ix = indexer.index(incremental=True)
                result["index_files"] = ix.get("files", 0)
                result["index_chunks"] = ix.get("chunks", 0)
            except Exception:
                pass
    return result
