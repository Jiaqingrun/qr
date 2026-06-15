"""macOS 原生窗口壳：用 pywebview 承载本地 Web 控制台。"""
from __future__ import annotations

import subprocess
import sys
import time
from typing import Any

from . import config, service_watch

APP_TITLE = "QR本地知识库"


def _endpoint() -> tuple[str, int]:
    cfg = config.load_config()
    host = str(cfg.get("web_host", "127.0.0.1"))
    port = int(cfg.get("web_port", 8765))
    return host, port


def ensure_web_running(*, timeout: float = 20.0) -> dict[str, Any]:
    """确保 Web 可访问；优先复用 launchd，否则后台拉起 qr web。"""
    host, port = _endpoint()
    url = f"http://{host}:{port}/"
    if service_watch.web_healthy(host, port, timeout=2.0):
        return {"ok": True, "url": url, "started": False}

    if service_watch.restart_web_service():
        deadline = time.time() + timeout
        while time.time() < deadline:
            if service_watch.web_healthy(host, port, timeout=2.0):
                return {"ok": True, "url": url, "started": True}
            time.sleep(0.5)

    config.ensure_dirs()
    log_path = config.LOGS_DIR / "web.log"
    argv = config.resolve_qr_argv() + ["web", "--port", str(port)]
    with open(log_path, "a", encoding="utf-8") as logf:
        subprocess.Popen(
            argv,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    deadline = time.time() + timeout
    while time.time() < deadline:
        if service_watch.web_healthy(host, port, timeout=2.0):
            return {"ok": True, "url": url, "started": True}
        time.sleep(0.5)

    return {"ok": False, "url": url, "detail": "Web 服务未响应"}


def open_in_browser() -> None:
    """用系统浏览器打开（旧行为，供 --browser 使用）。"""
    result = ensure_web_running()
    if not result["ok"]:
        _alert_macos(
            "QR本地知识库启动失败",
            "Web 服务未响应，请在终端运行：qr web --restart",
        )
        raise SystemExit(1)
    subprocess.run(["/usr/bin/open", result["url"]], check=False)


def open_native_window() -> None:
    """打开 pywebview 原生窗口；阻塞至用户关闭窗口。"""
    if sys.platform != "darwin":
        raise RuntimeError("原生窗口仅支持 macOS")

    try:
        import webview
    except ImportError as exc:
        _alert_macos(
            "QR本地知识库未安装 pywebview",
            "请在终端执行：conda activate qr && pip install -e ~/QR/dev/qr",
        )
        raise SystemExit(1) from exc

    result = ensure_web_running()
    if not result["ok"]:
        _alert_macos(
            "QR本地知识库启动失败",
            "Web 服务未响应，请在终端运行：qr web --restart",
        )
        raise SystemExit(1)

    webview.create_window(
        APP_TITLE,
        result["url"],
        width=1440,
        height=900,
        min_size=(960, 640),
        text_select=True,
    )
    webview.start(debug=False)


def _alert_macos(title: str, message: str) -> None:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    subprocess.run(
        [
            "osascript",
            "-e",
            f'display alert "{esc(title)}" message "{esc(message)}"',
        ],
        check=False,
    )
