from __future__ import annotations

import subprocess
import time
from pathlib import Path

from . import config, digest


def publish_digest(days: int = 1, *, notify: bool = True) -> dict:
    from . import changelog, proactive, workspace

    data = digest.generate(days=max(1, min(days, 30)))
    latest = config.LOGS_DIR / "digest-latest.md"
    proactive_alerts = proactive.collect_all()
    proactive.persist_digest(proactive_alerts)
    extra_lines: list[str] = []
    if proactive_alerts:
        extra_lines.extend(["", "## 主动提醒"])
        for a in proactive_alerts[:6]:
            extra_lines.append(f"- [{a.get('type', 'alert')}] {a.get('message', '')}")
    root = workspace.workspace_root()
    qr_dir = root / "dev" / "qr"
    if qr_dir.is_dir():
        try:
            cl = changelog.generate("dev/qr", days=max(days, 7))
            extra_lines.extend(["", "## 知识库项目简报", cl.get("content", "")[:800]])
        except Exception:
            pass
    content = data["content"] + "\n".join(extra_lines)
    latest.write_text(content, encoding="utf-8")
    out = {**data, "content": content, "latest": str(latest), "alerts": proactive_alerts}
    if notify:
        title = f"QR 知识库洞察 · {time.strftime('%m-%d', time.localtime())}"
        body = content.replace("\n", " ")[:180]
        if proactive_alerts:
            warn = [a for a in proactive_alerts if a.get("level") == "warn"]
            if warn:
                body = warn[0].get("message", body)[:180]
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
