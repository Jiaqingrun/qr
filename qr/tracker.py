from __future__ import annotations

import subprocess
import time

from . import db

SAMPLE_INTERVAL = 20      # 采样间隔(秒)
IDLE_THRESHOLD = 120      # 无操作超过该秒数视为空闲，不计时


def _frontmost_pyobjc():
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    return (str(app.localizedName() or "Unknown"),
            str(app.bundleIdentifier() or ""))


def _frontmost_osascript():
    try:
        out = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=5)
        name = out.stdout.strip()
        return (name, "") if name else None
    except Exception:
        return None


def get_frontmost():
    try:
        return _frontmost_pyobjc()
    except Exception:
        return _frontmost_osascript()


def _idle_quartz() -> float:
    import Quartz
    return float(Quartz.CGEventSourceSecondsSinceLastEventType(
        Quartz.kCGEventSourceStateHIDSystemState, Quartz.kCGAnyInputEventType))


def _idle_ioreg() -> float:
    try:
        import re
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem"],
                             capture_output=True, text=True, timeout=5).stdout
        m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
        return int(m.group(1)) / 1e9 if m else 0.0
    except Exception:
        return 0.0


def get_idle_seconds() -> float:
    try:
        return _idle_quartz()
    except Exception:
        return _idle_ioreg()


def sample() -> dict:
    idle = get_idle_seconds()
    front = get_frontmost()
    active = idle < IDLE_THRESHOLD and front is not None
    return {"idle": round(idle, 1), "app": front[0] if front else None,
            "bundle": front[1] if front else "", "active": active}


def run(interval: int = SAMPLE_INTERVAL, idle_threshold: int = IDLE_THRESHOLD) -> None:
    """常驻采样：跟踪焦点应用的使用会话，写入 app_usage。"""
    db.init_db()
    conn = db.connect()
    cur_app: str | None = None
    row_id: int | None = None
    start = 0
    try:
        while True:
            now = int(time.time())
            idle = get_idle_seconds()
            front = None if idle >= idle_threshold else get_frontmost()
            app = front[0] if front else None
            bundle = front[1] if front else ""
            if app is not None and app == cur_app and row_id is not None:
                conn.execute("UPDATE app_usage SET end_ts=?, duration=? WHERE id=?",
                             (now, now - start, row_id))
            else:
                cur_app = app
                start = now
                row_id = None
                if app is not None:
                    c = conn.execute(
                        "INSERT INTO app_usage(app,bundle,start_ts,end_ts,duration) "
                        "VALUES(?,?,?,?,0)", (app, bundle, now, now))
                    row_id = c.lastrowid
            conn.commit()
            time.sleep(interval)
    finally:
        conn.close()
