from __future__ import annotations

from . import cursor, files, gitlog, notes, shell

COLLECTORS = {
    "shell": shell.collect,
    "git": gitlog.collect,
    "files": files.collect,
    "cursor": cursor.collect,
}


def run(conn, sources: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for name in sources:
        fn = COLLECTORS.get(name)
        if fn is None:
            continue
        result[name] = fn(conn)
    return result
