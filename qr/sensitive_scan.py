"""M10-2 · Cursor 归档敏感模式检测（提示，不自动删内容）。"""
from __future__ import annotations

import re
from typing import Any

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key (AKIA…)"),
    (re.compile(r"-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----"), "SSH/TLS 私钥块"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "GitHub personal token"),
    (re.compile(r"gho_[A-Za-z0-9]{20,}"), "GitHub OAuth token"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "sk- 前缀 API 密钥"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
]

_SENSITIVE_HINT = (
    "含疑似密钥或令牌；请从 Cursor 归档/时间线源文件中删除，勿提交 Git。"
    " 详见 `qr cursor sanitize`。"
)


def scan_text(text: str) -> list[str]:
    """返回命中的模式标签（去重保序）。"""
    if not (text or "").strip():
        return []
    seen: set[str] = set()
    out: list[str] = []
    for pat, label in _PATTERNS:
        if pat.search(text) and label not in seen:
            seen.add(label)
            out.append(label)
    return out


def meta_patch_for_content(*parts: str) -> dict[str, Any]:
    """供 cursor 采集写入 meta 的字段。"""
    labels = scan_text("\n".join(p for p in parts if p))
    if not labels:
        return {}
    return {
        "sensitive_warning": True,
        "sensitive_patterns": labels,
        "sensitive_hint": _SENSITIVE_HINT,
    }


def scan_cursor_events(conn) -> list[dict[str, Any]]:
    """扫描库内 cursor 事件标题/内容（归档路径旁的内联文本）。"""
    rows = conn.execute(
        "SELECT uid, title, content, meta FROM events WHERE source='cursor' "
        "ORDER BY ts DESC LIMIT 5000"
    ).fetchall()
    hits: list[dict[str, Any]] = []
    for r in rows:
        blob = f"{r['title'] or ''}\n{r['content'] or ''}"
        labels = scan_text(blob)
        if not labels:
            continue
        hits.append({
            "uid": r["uid"],
            "patterns": labels,
            "title": (r["title"] or "")[:80],
        })
    return hits
