"""launchd 定时任务安装（供 CLI 与 Web 运维页共用）。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from . import config, service_watch

WEB_LABEL = "com.qr.web"
WEB_WATCH_LABEL = "com.qr.web-watch"
AGENT_LABELS = [
    "com.qr.tracker",
    "com.qr.cursor",
    "com.qr.auto",
    "com.qr.weekly",
    "com.qr.daily",
    "com.qr.eval",
    WEB_LABEL,
    WEB_WATCH_LABEL,
]
LEGACY_AGENT_LABELS = [
    "com.qr.kb.tracker",
    "com.qr.kb.cursor",
    "com.qr.kb.auto",
    "com.qr.kb.weekly",
    "com.qr.kb.daily",
    "com.qr.kb.web",
]


def uninstall_launch_agent(label: str) -> None:
    path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    subprocess.call(["launchctl", "unload", str(path)], stderr=subprocess.DEVNULL)
    if path.exists():
        path.unlink()


def uninstall_legacy_agents() -> None:
    for label in LEGACY_AGENT_LABELS:
        uninstall_launch_agent(label)


def plist(
    label: str,
    args: list[str],
    *,
    interval: int | None = None,
    calendar: dict | None = None,
    run_at_load: bool = False,
    keepalive: bool = False,
    throttle: int | None = None,
) -> str:
    qr_argv = config.resolve_qr_argv()
    arg_xml = "\n".join(f"    <string>{a}</string>" for a in [*qr_argv, *args])
    if keepalive:
        trigger = "  <key>KeepAlive</key><true/>"
    elif interval is not None:
        trigger = f"  <key>StartInterval</key><integer>{interval}</integer>"
    else:
        cal = "".join(
            f"    <key>{k}</key><integer>{v}</integer>\n"
            for k, v in (calendar or {}).items()
        )
        trigger = f"  <key>StartCalendarInterval</key>\n  <dict>\n{cal}  </dict>"
    throttle_xml = ""
    if throttle is not None:
        throttle_xml = f"  <key>ThrottleInterval</key><integer>{throttle}</integer>\n"
    name = label.rsplit(".", 1)[-1]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
{arg_xml}
  </array>
{trigger}
{throttle_xml}  <key>StandardOutPath</key><string>{config.LOGS_DIR / (name + '.out.log')}</string>
  <key>StandardErrorPath</key><string>{config.LOGS_DIR / (name + '.err.log')}</string>
  <key>RunAtLoad</key><{'true' if run_at_load else 'false'}/>
</dict>
</plist>
"""


def install_agent(label: str, plist_text: str) -> None:
    path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plist_text)
    subprocess.call(["launchctl", "unload", str(path)], stderr=subprocess.DEVNULL)
    subprocess.call(["launchctl", "load", str(path)], stderr=subprocess.DEVNULL)


def web_service_args(host: str, port: int) -> list[str]:
    return ["web", "--host", host, "--port", str(port)]


def install_web_watch_service() -> None:
    cfg = config.load_config()
    sec = max(30, int(cfg.get("web_watch_seconds", 45)))
    install_agent(
        WEB_WATCH_LABEL,
        plist(
            WEB_WATCH_LABEL,
            ["web-watch"],
            interval=sec,
            run_at_load=True,
            throttle=20,
        ),
    )


def install_web_agents(host: str | None = None, port: int | None = None) -> None:
    """重装 Web launchd 服务；会中断当前由 com.qr.web 提供的 HTTP 连接。"""
    cfg = config.load_config()
    host = host or cfg.get("web_host", "127.0.0.1")
    port = port or int(cfg.get("web_port", 8765))
    config.ensure_dirs()
    install_agent(
        WEB_LABEL,
        plist(
            WEB_LABEL,
            web_service_args(host, port),
            keepalive=True,
            run_at_load=True,
            throttle=15,
        ),
    )
    install_web_watch_service()


def install_core_agents(
    *,
    every_hours: float = 2,
    weekday: int = 1,
    hour: int = 9,
    daily: bool = False,
    eval_monthly: bool = True,
    eval_day: int = 1,
    eval_hour: int = 3,
) -> dict[str, Any]:
    """安装采集/同步/总结任务（不含 Web 常驻）。"""
    uninstall_legacy_agents()
    config.ensure_dirs()
    interval = max(300, int(every_hours * 3600))
    installed: list[str] = []

    install_agent(
        "com.qr.tracker",
        plist("com.qr.tracker", ["track"], keepalive=True, run_at_load=True),
    )
    installed.append("com.qr.tracker")

    cursor_sec = max(15, int(config.load_config().get("cursor_poll_seconds", 60)))
    install_agent(
        "com.qr.cursor",
        plist(
            "com.qr.cursor",
            ["cursor-watch"],
            interval=cursor_sec,
            run_at_load=True,
        ),
    )
    installed.append("com.qr.cursor")

    install_agent(
        "com.qr.auto",
        plist("com.qr.auto", ["update"], interval=interval, run_at_load=True),
    )
    installed.append("com.qr.auto")

    install_agent(
        "com.qr.weekly",
        plist(
            "com.qr.weekly",
            ["update", "--summary", "week"],
            calendar={"Weekday": weekday, "Hour": hour, "Minute": 0},
        ),
    )
    installed.append("com.qr.weekly")

    if daily:
        install_agent(
            "com.qr.daily",
            plist(
                "com.qr.daily",
                ["summary", "--period", "day", "--no-show"],
                calendar={"Hour": hour, "Minute": 30},
            ),
        )
        installed.append("com.qr.daily")

    if eval_monthly:
        install_agent(
            "com.qr.eval",
            plist(
                "com.qr.eval",
                ["eval", "run"],
                calendar={"Day": eval_day, "Hour": eval_hour, "Minute": 0},
            ),
        )
        installed.append("com.qr.eval")

    return {
        "ok": True,
        "installed": installed,
        "every_hours": every_hours,
        "cursor_poll_seconds": cursor_sec,
        "eval_monthly": eval_monthly,
        "eval_day": eval_day,
        "eval_hour": eval_hour,
    }


def install_all(
    *,
    every_hours: float = 2,
    weekday: int = 1,
    hour: int = 9,
    daily: bool = False,
    eval_monthly: bool = True,
    eval_day: int | None = None,
    eval_hour: int | None = None,
    include_web: bool = True,
) -> dict[str, Any]:
    cfg = config.load_config()
    if eval_day is None:
        eval_day = int(cfg.get("eval_monthly_day", 1))
    if eval_hour is None:
        eval_hour = int(cfg.get("eval_monthly_hour", 3))
    core = install_core_agents(
        every_hours=every_hours,
        weekday=weekday,
        hour=hour,
        daily=daily,
        eval_monthly=eval_monthly,
        eval_day=eval_day,
        eval_hour=eval_hour,
    )
    if include_web:
        cfg = config.load_config()
        install_web_agents(
            host=cfg.get("web_host", "127.0.0.1"),
            port=int(cfg.get("web_port", 8765)),
        )
        core["web_installed"] = True
    else:
        core["web_installed"] = False
    return core


def uninstall_agent(label: str) -> dict[str, Any]:
    """卸载单个 launchd 任务。"""
    if label not in AGENT_LABELS:
        return {"ok": False, "error": f"未知任务: {label}"}
    uninstall_launch_agent(label)
    return {"ok": True, "label": label}


def uninstall_all_agents() -> None:
    for label in AGENT_LABELS:
        uninstall_launch_agent(label)
    uninstall_legacy_agents()


def uninstall_web_agents() -> None:
    for label in (WEB_WATCH_LABEL, WEB_LABEL):
        uninstall_launch_agent(label)


def restart_web_service() -> bool:
    """重启已安装的 launchd Web 服务。"""
    path = Path.home() / "Library" / "LaunchAgents" / f"{WEB_LABEL}.plist"
    if not path.exists():
        return False
    uid = os.getuid()
    label = f"gui/{uid}/{WEB_LABEL}"
    r = subprocess.run(
        ["launchctl", "kickstart", "-k", label],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        subprocess.call(["launchctl", "unload", str(path)], stderr=subprocess.DEVNULL)
        subprocess.call(["launchctl", "load", str(path)], stderr=subprocess.DEVNULL)
    return True


def agent_rows() -> list[tuple[str, str]]:
    """返回 (label, 状态文案)。"""
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    rows: list[tuple[str, str]] = []
    for label in AGENT_LABELS:
        path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        loaded = any(label in line for line in out.splitlines())
        if loaded:
            state = "运行中"
        elif path.exists():
            state = "已安装未加载"
        else:
            state = "未安装"
        rows.append((label, state))
    return rows


def web_probe(host: str | None = None, port: int | None = None) -> dict[str, Any]:
    cfg = config.load_config()
    host = host or cfg.get("web_host", "127.0.0.1")
    port = int(port if port is not None else cfg.get("web_port", 8765))
    path = Path.home() / "Library" / "LaunchAgents" / f"{WEB_LABEL}.plist"
    watch_path = Path.home() / "Library" / "LaunchAgents" / f"{WEB_WATCH_LABEL}.plist"
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    loaded = any(WEB_LABEL in line for line in out.splitlines())
    watch_loaded = any(WEB_WATCH_LABEL in line for line in out.splitlines())
    probe = service_watch.probe_web(host, port)
    return {
        "host": host,
        "port": port,
        "plist_exists": path.exists(),
        "watch_plist_exists": watch_path.exists(),
        "loaded": loaded,
        "watch_loaded": watch_loaded,
        "probe": probe,
    }
