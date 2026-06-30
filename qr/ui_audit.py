"""Web UI 静态自检：按钮/搜索框绑定、API 路径、重复 ID。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_STATIC = Path(__file__).resolve().parent / "static"
_WEB_PY = Path(__file__).resolve().parent / "web.py"

# 需有 change/keydown/关联按钮 的搜索与筛选框
_SEARCH_INPUTS = (
    "tlSearch",
    "askSearch",
    "qInput",
    "noteInput",
)
_FILTER_SELECTS = (
    "tlSource",
    "tlProject",
    "tlSort",
    "tlDateFrom",
    "tlDateTo",
    "askProject",
    "askCategory",
    "qProject",
    "qCategory",
    "pgProjectFilter",
    "uPeriod",
)

_BTN_ID_RE = re.compile(r'id="([^"]*(?:Btn|btn)[^"]*)"')
_API_CALL_RE = re.compile(r"""api\(\s*['"`]([^'"`]+)""")
_FETCH_CALL_RE = re.compile(r"""fetch\(\s*['"`]([^'"`]+)""")
_ROUTE_RE = re.compile(r"""@app\.(?:get|post|put|delete|patch)\(\s*["']([^"']+)""")


def _read_ui_sources() -> tuple[str, str]:
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    features = (_STATIC / "js" / "qr-features.js").read_text(encoding="utf-8")
    core = (_STATIC / "js" / "qr-core.js").read_text(encoding="utf-8")
    nav = (_STATIC / "js" / "qr-nav.js").read_text(encoding="utf-8")
    script_m = re.search(r"<script>\s*(.*?)\s*</script>\s*<script src=", html, re.S)
    inline = script_m.group(1) if script_m else ""
    return html, inline + "\n" + features + "\n" + core + "\n" + nav


def _routes_from_web() -> list[str]:
    text = _WEB_PY.read_text(encoding="utf-8")
    return _ROUTE_RE.findall(text)


def _route_matches(path: str, routes: list[str]) -> bool:
    base = path.split("?", 1)[0].rstrip("/") or "/"
    if base in routes:
        return True
    for r in routes:
        pat = "^" + re.sub(r"\{[^}]+\}", r"[^/]+", r.rstrip("/") or "/") + "$"
        if re.match(pat, base):
            return True
    return False


def _has_handler(element_id: str, js: str) -> bool:
    patterns = (
        rf'\$\([\'"]#{re.escape(element_id)}[\'"]\)',
        rf'getElementById\([\'"]{re.escape(element_id)}[\'"]\)',
        rf'id="{re.escape(element_id)}"[^>]*\bonclick\b',
        rf'#{re.escape(element_id)}\)?\.(?:onclick|addEventListener)',
        rf'[\'"]#{re.escape(element_id)}[\'"].*addEventListener',
        rf'querySelector\([\'"]#{re.escape(element_id)}[\'"]\)',
    )
    return any(re.search(p, js) for p in patterns)


def _has_search_binding(element_id: str, js: str) -> bool:
    if element_id == "qInput":
        return "qBtn" in js and "qInput" in js
    if element_id == "noteInput":
        return "noteBtn" in js
    eid = re.escape(element_id)
    return bool(
        re.search(rf"getElementById\(['\"]{eid}['\"]\)", js)
        or re.search(rf"\$\(['\"]#{eid}['\"]\)", js)
        or re.search(rf"id=\"{eid}\"", js)
    )


def _html_without_scripts(html: str) -> str:
    return re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.I | re.S)


def _duplicate_ids(html: str) -> list[str]:
    markup = _html_without_scripts(html)
    # 勿把 data-id 当成 id（\b 会落在 data-id 的 id 前）
    ids = re.findall(r'(?:^|\s)id="([^"]+)"', markup)
    seen: dict[str, int] = {}
    for i in ids:
        if "${" in i or "+" in i:
            continue
        seen[i] = seen.get(i, 0) + 1
    return sorted(k for k, v in seen.items() if v > 1)


def audit_ui(*, strict_api: bool = False) -> dict[str, Any]:
    """扫描静态 UI；返回 issues 列表与统计。"""
    html, js = _read_ui_sources()
    routes = _routes_from_web()
    issues: list[dict[str, str]] = []

    btn_ids = sorted(set(_BTN_ID_RE.findall(html)))
    for bid in btn_ids:
        if not _has_handler(bid, js):
            issues.append({
                "level": "error",
                "area": "ui_button",
                "id": bid,
                "message": f"按钮 #{bid} 未找到 JS 事件绑定",
            })

    for sid in _SEARCH_INPUTS:
        if f'id="{sid}"' not in html:
            issues.append({
                "level": "warn",
                "area": "ui_search",
                "id": sid,
                "message": f"搜索框 #{sid} 在 HTML 中缺失",
            })
        elif not _has_search_binding(sid, js):
            issues.append({
                "level": "error",
                "area": "ui_search",
                "id": sid,
                "message": f"搜索框 #{sid} 未绑定 Enter/检索逻辑",
            })

    for fid in _FILTER_SELECTS:
        if f'id="{fid}"' not in html:
            continue
        if not _has_handler(fid, js) and fid not in js:
            issues.append({
                "level": "warn",
                "area": "ui_filter",
                "id": fid,
                "message": f"筛选 #{fid} 可能未绑定 change 事件",
            })

    dups = _duplicate_ids(html)
    for did in dups:
        issues.append({
            "level": "error",
            "area": "ui_html",
            "id": did,
            "message": f"重复 id=\"{did}\"",
        })

    if "pointer-events:none" in html and "#tlOut.tl-loading .tl-item" in html:
        issues.append({
            "level": "error",
            "area": "ui_timeline",
            "id": "tlOut",
            "message": "时间线加载样式使用 pointer-events:none，可能导致无法点击",
        })

    if "finally" not in js or "tlLoadGen" not in js or "tl-loading" not in js:
        issues.append({
            "level": "warn",
            "area": "ui_timeline",
            "id": "loadTimeline",
            "message": "时间线加载逻辑可能缺少 tl-loading 清理",
        })

    # api() 应自动补 Content-Type
    if "Content-Type" not in (_STATIC / "js" / "qr-core.js").read_text(encoding="utf-8"):
        issues.append({
            "level": "error",
            "area": "ui_api",
            "id": "api",
            "message": "qr-core.js 的 api() 未自动设置 JSON Content-Type",
        })

    api_paths: set[str] = set()
    for m in _API_CALL_RE.finditer(js):
        api_paths.add(m.group(1).split("?", 1)[0])
    for m in _FETCH_CALL_RE.finditer(js):
        p = m.group(1).split("?", 1)[0]
        if p.startswith("/api/"):
            api_paths.add(p)

    missing_api: list[str] = []
    for p in sorted(api_paths):
        if "/+" in p or p.endswith("/"):
            continue
        if not _route_matches(p, routes):
            missing_api.append(p)

    if strict_api and missing_api:
        for p in missing_api:
            issues.append({
                "level": "error",
                "area": "ui_api",
                "id": p,
                "message": f"前端调用 {p} 在后端无对应路由",
            })

    ok = not any(i["level"] == "error" for i in issues)
    return {
        "ok": ok,
        "buttons": len(btn_ids),
        "search_inputs": len(_SEARCH_INPUTS),
        "api_calls_checked": len(api_paths),
        "missing_api": missing_api,
        "issues": issues,
    }


def format_issues(issues: list[dict[str, str]], *, limit: int = 12) -> list[str]:
    lines: list[str] = []
    for i in issues[:limit]:
        lines.append(f"[{i.get('level', '?')}] {i.get('area')}: {i.get('message')}")
    if len(issues) > limit:
        lines.append(f"… 另有 {len(issues) - limit} 项")
    return lines
