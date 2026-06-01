from __future__ import annotations

from . import db


def _fmt(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m}m"
    if m:
        return f"{m}m{s}s"
    return f"{s}s"


def stats(start: int, end: int) -> tuple[list[dict], int]:
    with db.session() as conn:
        rows = conn.execute(
            "SELECT app, SUM(duration) d, COUNT(*) n FROM app_usage "
            "WHERE start_ts>=? AND start_ts<=? GROUP BY app ORDER BY d DESC",
            (start, end),
        ).fetchall()
    total = sum(r["d"] for r in rows)
    out = []
    for r in rows:
        if not r["d"]:
            continue
        out.append({"app": r["app"], "seconds": int(r["d"]),
                    "human": _fmt(r["d"]), "sessions": r["n"],
                    "pct": round(100.0 * r["d"] / total, 1) if total else 0.0})
    return out, int(total)


def digest(start: int, end: int, top: int = 12) -> str:
    rows, total = stats(start, end)
    if not rows:
        return ""
    lines = [f"【应用使用】活跃总时长 {_fmt(total)}，按时长排序:"]
    for r in rows[:top]:
        lines.append(f"  - {r['app']}: {r['human']} ({r['pct']}%, {r['sessions']} 次切入)")
    return "\n".join(lines)
