"""本地服务健康探测与 launchd 自动拉起（主要面向 Web）。"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import config

WEB_LABEL = "com.qr.web"


def _endpoint(host: str | None = None, port: int | None = None) -> tuple[str, int]:
    cfg = config.load_config()
    h = host or str(cfg.get("web_host", "127.0.0.1"))
    p = int(port if port is not None else cfg.get("web_port", 8765))
    return h, p


def probe_web(
    host: str | None = None,
    port: int | None = None,
    *,
    timeout: float = 4.0,
) -> dict[str, Any]:
    """检测端口监听与 /api/status 是否可用。"""
    h, p = _endpoint(host, port)
    out: dict[str, Any] = {
        "host": h,
        "port": p,
        "url": f"http://{h}:{p}/",
        "listening": False,
        "http_ok": False,
        "health_ok": None,
        "detail": "",
    }
    try:
        with socket.create_connection((h, p), timeout=timeout):
            out["listening"] = True
    except OSError as exc:
        out["detail"] = str(exc)
        return out

    status_url = f"http://{h}:{p}/api/status"
    try:
        req = urllib.request.Request(status_url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(8192).decode("utf-8", errors="replace")
            out["http_ok"] = resp.status == 200
            if out["http_ok"]:
                try:
                    data = json.loads(body)
                    out["health_ok"] = bool(data.get("health_ok", True))
                    if data.get("error"):
                        out["health_ok"] = False
                        out["detail"] = str(data["error"])[:200]
                except json.JSONDecodeError:
                    out["health_ok"] = True
            else:
                out["detail"] = f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        out["detail"] = f"HTTP {exc.code}"
    except Exception as exc:
        out["detail"] = str(exc)
    return out


def web_healthy(host: str | None = None, port: int | None = None, *, timeout: float = 4.0) -> bool:
    p = probe_web(host, port, timeout=timeout)
    if not (p["listening"] and p["http_ok"]):
        return False
    if p.get("health_ok") is False:
        return False
    return True


def restart_web_service() -> bool:
    """通过 launchctl 重启 com.qr.web。"""
    path = Path.home() / "Library/LaunchAgents" / f"{WEB_LABEL}.plist"
    if not path.exists():
        return False
    uid = os.getuid()
    target = f"gui/{uid}/{WEB_LABEL}"
    r = subprocess.run(
        ["launchctl", "kickstart", "-k", target],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        subprocess.call(["launchctl", "bootout", target], stderr=subprocess.DEVNULL)
        subprocess.call(["launchctl", "bootstrap", f"gui/{uid}", str(path)], stderr=subprocess.DEVNULL)
        r = subprocess.run(
            ["launchctl", "kickstart", target],
            capture_output=True,
            text=True,
        )
    return r.returncode == 0


def watch_web_once(
    *,
    host: str | None = None,
    port: int | None = None,
    restart: bool = True,
    settle_seconds: float = 8.0,
) -> dict[str, Any]:
    """探测一次；异常时尝试 kickstart Web。"""
    probe = probe_web(host, port)
    healthy = web_healthy(host, port)
    restarted = False
    if not healthy and restart:
        restarted = restart_web_service()
        if restarted and settle_seconds > 0:
            time.sleep(settle_seconds)
            probe = probe_web(host, port)
            healthy = web_healthy(host, port)
    return {
        "healthy": healthy,
        "restarted": restarted,
        "probe": probe,
    }


def run_web_watch_loop(interval: int | None = None) -> None:
    """供 launchd 周期性调用。"""
    cfg = config.load_config()
    sec = max(30, int(interval or cfg.get("web_watch_seconds", 45)))
    while True:
        try:
            watch_web_once()
        except Exception:
            pass
        time.sleep(sec)
