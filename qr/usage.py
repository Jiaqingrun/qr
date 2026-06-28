from __future__ import annotations

import re

from . import config, db

_GAME_APP_RE = re.compile(
    r"steam|epic\s*games?|battle\.?net|blizzard|gog\s*galaxy|riot\s*client|"
    r"minecraft|roblox|"
    r"eve\s*online|paradox\s*launcher|craft\s*the\s*world|"
    r"stellaris|crusader\s*kings|hearts\s*of\s*iron|europa\s*universalis|"
    r"genshin|starcraft|warcraft|diablo|overwatch|"
    r"league\s*of\s*legends|valorant|fortnite|"
    r"nintendo|playstation|xbox|原神|魔兽世界|炉石",
    re.I,
)
_GAME_BUNDLE_RE = re.compile(
    r"com\.valvesoftware\.|com\.paradoxplaza\.|com\.ccp\.|"
    r"com\.blizzard\.|com\.epicgames\.|com\.mojang\.|"
    r"com\.feralinteractive\.|com\.aspyr\.|com\.hinterland\.|"
    r"com\.riotgames\.|net\.steam\.|com\.steampowered\.|"
    r"com\.apple\.gamecenter|com\.microsoft\.minecraft|"
    r"com\.supercell\.|com\.tencent\.game",
    re.I,
)


def _fmt(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m}m"
    if m:
        return f"{m}m{s}s"
    return f"{s}s"


def is_excluded_usage(app: str | None, bundle: str | None = None, cfg: dict | None = None) -> bool:
    """统计/展示时是否排除（含游戏过滤，见 standards §五·本机使用统计）。"""
    cfg = cfg or config.load_config()
    app_s = (app or "").strip()
    bundle_s = (bundle or "").strip()
    include = {str(x).casefold() for x in (cfg.get("usage_include_apps") or []) if str(x).strip()}
    if app_s and app_s.casefold() in include:
        return False
    for item in cfg.get("usage_exclude_apps") or []:
        if item and item.lower() in app_s.lower():
            return True
    for item in cfg.get("usage_exclude_bundles") or []:
        if item and item.lower() in bundle_s.lower():
            return True
    if not cfg.get("usage_exclude_games", True):
        return False
    if bundle_s and _GAME_BUNDLE_RE.search(bundle_s):
        return True
    if app_s and _GAME_APP_RE.search(app_s):
        return True
    return False


def stats(start: int, end: int) -> tuple[list[dict], int]:
    with db.session() as conn:
        rows = conn.execute(
            "SELECT app, bundle, SUM(duration) d, COUNT(*) n FROM app_usage "
            "WHERE start_ts>=? AND start_ts<=? GROUP BY app, bundle",
            (start, end),
        ).fetchall()
    cfg = config.load_config()
    merged: dict[str, dict] = {}
    for r in rows:
        if not r["d"] or is_excluded_usage(r["app"], r["bundle"], cfg):
            continue
        app = r["app"] or ""
        slot = merged.setdefault(app, {"app": app, "seconds": 0, "sessions": 0})
        slot["seconds"] += int(r["d"])
        slot["sessions"] += int(r["n"])
    out = sorted(merged.values(), key=lambda x: x["seconds"], reverse=True)
    total = sum(x["seconds"] for x in out)
    for row in out:
        row["human"] = _fmt(row["seconds"])
        row["pct"] = round(100.0 * row["seconds"] / total, 1) if total else 0.0
    return out, int(total)


def digest(start: int, end: int, top: int = 12) -> str:
    rows, total = stats(start, end)
    if not rows:
        return ""
    lines = [f"【应用使用】活跃总时长 {_fmt(total)}（不含游戏），按时长排序:"]
    for r in rows[:top]:
        lines.append(f"  - {r['app']}: {r['human']} ({r['pct']}%, {r['sessions']} 次切入)")
    return "\n".join(lines)
