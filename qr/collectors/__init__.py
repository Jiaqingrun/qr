from __future__ import annotations

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
    return result
