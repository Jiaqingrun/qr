from __future__ import annotations

import calendar
import datetime
import re
import time

_TS_TAG_RE = re.compile(r"<timestamp>(.*?)</timestamp>", re.DOTALL)
_TZ_SUFFIX_RE = re.compile(r"\s*\(UTC(?:([+-]\d{1,2}))?\)\s*$", re.IGNORECASE)
_CURSOR_FORMATS = (
    "%A, %B %d, %Y, %I:%M %p",
    "%A, %B %d, %Y, %H:%M",
)


def format_local(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def parse_day(s: str) -> datetime.datetime:
    return datetime.datetime.strptime(s, "%Y-%m-%d")


def day_start_local(day: datetime.datetime) -> int:
    return int(time.mktime(day.timetuple()))


def day_end_exclusive_local(day: datetime.datetime) -> int:
    return int(time.mktime((day + datetime.timedelta(days=1)).timetuple()))


def file_time_bounds(path) -> tuple[int, int]:
    """文件创建/修改时间范围，用于无绝对时间戳时的有序估算。"""
    st = path.stat()
    start = int(getattr(st, "st_birthtime", st.st_mtime))
    end = int(st.st_mtime)
    if end < start:
        start = end
    return start, end


def parse_cursor_timestamp(text: str) -> int | None:
    """解析 Cursor 对话中的 <timestamp>，正确处理 UTC 与 UTC+8。"""
    m = _TS_TAG_RE.search(text or "")
    if not m:
        return None
    raw = m.group(1).strip()
    utc_wall = False
    tz = _TZ_SUFFIX_RE.search(raw)
    if tz:
        raw = raw[: tz.start()].strip()
        utc_wall = tz.group(1) is None
    dt = None
    for fmt in _CURSOR_FORMATS:
        try:
            dt = datetime.datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        iso = raw.replace("Z", "+00:00")
        try:
            parsed = datetime.datetime.fromisoformat(iso)
            if parsed.tzinfo is not None:
                return int(parsed.timestamp())
            dt = parsed
        except ValueError:
            return None
    if utc_wall:
        return int(calendar.timegm(dt.timetuple()))
    return int(time.mktime(dt.timetuple()))


def interpolate_series(
    count: int,
    known: dict[int, int],
    *,
    start_ts: int,
    end_ts: int,
    step_seconds: int = 60,
) -> list[int]:
    """按序号在已知时间锚点之间线性插值。"""
    if count <= 0:
        return []
    if not known:
        if count == 1:
            return [end_ts]
        span = max(end_ts - start_ts, 1)
        return [int(start_ts + span * i / (count - 1)) for i in range(count)]
    anchors = sorted(known.items())
    out: list[int | None] = [None] * count
    for i, ts in anchors:
        if 0 <= i < count:
            out[i] = ts
    first_i, first_ts = anchors[0]
    last_i, last_ts = anchors[-1]
    for i in range(count):
        if out[i] is not None:
            continue
        prev = next(((j, t) for j, t in reversed(anchors) if j < i), None)
        nxt = next(((j, t) for j, t in anchors if j > i), None)
        if prev and nxt:
            j0, t0 = prev
            j1, t1 = nxt
            ratio = (i - j0) / (j1 - j0)
            out[i] = int(t0 + (t1 - t0) * ratio)
        elif prev:
            j0, t0 = prev
            out[i] = t0 + (i - j0) * step_seconds
        elif nxt:
            j1, t1 = nxt
            out[i] = t1 - (j1 - i) * step_seconds
        elif i < first_i:
            out[i] = first_ts - (first_i - i) * step_seconds
        elif i > last_i:
            tail_n = count - last_i
            pos = i - last_i
            span = max(end_ts - last_ts, 1)
            if tail_n <= 1:
                out[i] = end_ts
            else:
                out[i] = int(last_ts + span * pos / tail_n)
        else:
            out[i] = end_ts
    return [int(t) for t in out]
