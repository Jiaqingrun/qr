from __future__ import annotations

import subprocess
import time
from pathlib import Path

from . import config, digest


def publish_digest(days: int = 1, *, notify: bool = True) -> dict:
    data = digest.generate(days=max(1, min(days, 30)))
    latest = config.LOGS_DIR / "digest-latest.md"
    latest.write_text(data["content"], encoding="utf-8")
    out = {**data, "latest": str(latest)}
    if notify:
        title = f"QR 知识库洞察 · {time.strftime('%m-%d', time.localtime())}"
        body = (data["content"] or "").replace("\n", " ")[:180]
        out["notified"] = _mac_notify(title, body)
    else:
        out["notified"] = False
    return out


def notify(title: str, body: str = "") -> bool:
    """发送 macOS 系统通知（非 macOS 或失败时返回 False）。"""
    t = (title or "QR 知识库").replace("\n", " ").strip()[:120]
    b = (body or "").replace("\n", " ").strip()[:200]
    if not t:
        t = "QR 知识库"
    return _mac_notify(t, b)


def _mac_notify(title: str, body: str) -> bool:
    safe_title = title.replace('"', "'")
    safe_body = body.replace('"', "'")
    script = f'display notification "{safe_body}" with title "{safe_title}"'
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=5)
        return True
    except (subprocess.SubprocessError, OSError):
        return False
