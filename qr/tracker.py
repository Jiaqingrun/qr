from __future__ import annotations

import subprocess
import time

from . import db, config

SAMPLE_INTERVAL = 20      # 采样间隔(秒)
IDLE_THRESHOLD = 120      # 无操作超过该秒数视为空闲，不计时


def parse_pause_duration(spec: str) -> int:
    """解析 2h / 30m / 1d / off → 秒数；off 返回 0。"""
    s = (spec or "").strip().lower()
    if s in ("off", "0", "clear", "resume", "none"):
        return 0
    if s.isdigit():
        return int(s)
    num = ""
    unit = ""
    for ch in s:
        if ch.isdigit():
            num += ch
        else:
            unit += ch
    if not num:
        raise ValueError(f"无法解析时长: {spec}")
    n = int(num)
    u = unit or "s"
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(u)
    if mult is None:
        raise ValueError(f"未知单位: {unit}")
    return n * mult


def pause_status() -> dict:
    """当前暂停状态（tracker_pause_until）。"""
    cfg = config.load_config()
    until = int(cfg.get("tracker_pause_until") or 0)
    now = int(time.time())
    if until <= now:
        return {"paused": False, "until": 0, "until_human": "", "remaining_s": 0}
    return {
        "paused": True,
        "until": until,
        "until_human": time.strftime("%Y-%m-%d %H:%M", time.localtime(until)),
        "remaining_s": until - now,
    }


def set_pause(duration_spec: str) -> dict:
    """设置或清除暂停；duration_spec 如 2h、30m、off。"""
    secs = parse_pause_duration(duration_spec)
    cfg = config.load_config()
    if secs <= 0:
        cfg["tracker_pause_until"] = 0
        config.save_config(cfg)
        return {"paused": False, "until": 0, "message": "已恢复屏幕采样"}
    until = int(time.time()) + secs
    cfg["tracker_pause_until"] = until
    config.save_config(cfg)
    human = time.strftime("%Y-%m-%d %H:%M", time.localtime(until))
    return {
        "paused": True,
        "until": until,
        "until_human": human,
        "message": f"已暂停至 {human}",
    }


def is_tracking_paused() -> bool:
    return pause_status()["paused"]


def should_record_app(app: str, bundle: str) -> bool:
    """是否写入 app_usage（暂停或排除列表命中则不写）。"""
    if is_tracking_paused():
        return False
    cfg = config.load_config()
    app_l = (app or "").lower()
    bundle_l = (bundle or "").lower()
    for item in cfg.get("tracker_exclude_apps") or []:
        if str(item).lower() in app_l:
            return False
    for item in cfg.get("tracker_exclude_bundles") or []:
        if str(item).lower() in bundle_l:
            return False
    return True


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
    out = {"idle": round(idle, 1), "app": front[0] if front else None,
           "bundle": front[1] if front else "", "active": active}
    if front is None and idle < IDLE_THRESHOLD:
        out["warn"] = "无法读取前台应用（检查辅助功能/自动化权限）"
    return out


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
            if idle < idle_threshold and front is None:
                db.set_state(conn, "tracker_last_error",
                             "无法读取前台应用；请 qr permissions open")
            else:
                db.set_state(conn, "tracker_last_error", "")
                db.set_state(conn, "tracker_last_ok", str(now))
            app = front[0] if front else None
            bundle = front[1] if front else ""
            if app is not None and app == cur_app and row_id is not None:
                if should_record_app(app, bundle):
                    conn.execute("UPDATE app_usage SET end_ts=?, duration=? WHERE id=?",
                                 (now, now - start, row_id))
            else:
                cur_app = app
                start = now
                row_id = None
                if app is not None and should_record_app(app, bundle):
                    c = conn.execute(
                        "INSERT INTO app_usage(app,bundle,start_ts,end_ts,duration) "
                        "VALUES(?,?,?,?,0)", (app, bundle, now, now))
                    row_id = c.lastrowid
            conn.commit()
            time.sleep(interval)
    finally:
        conn.close()
