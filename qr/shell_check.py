from __future__ import annotations

import os
import re
from pathlib import Path

from . import config, db


def _read_zshrc() -> str:
    path = Path.home() / ".zshrc"
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


_MARKER = "# QR本地知识库：zsh 历史带时间戳"
_SNIPPET_LINES = (
    _MARKER,
    "setopt EXTENDED_HISTORY",
    "setopt INC_APPEND_HISTORY",
    "setopt SHARE_HISTORY",
    "setopt HIST_IGNORE_DUPS",
    'export HISTFILE="${HISTFILE:-$HOME/.zsh_history}"',
    "HISTSIZE=100000",
    "SAVEHIST=100000",
)


def enable_extended_history() -> dict:
    """在 ~/.zshrc 中启用带 epoch 的 zsh 历史（幂等）。"""
    path = Path.home() / ".zshrc"
    text = _read_zshrc()
    changed = False
    if re.search(r"setopt\s+EXTENDED_HISTORY", text, re.I):
        if not re.search(r"setopt\s+SHARE_HISTORY", text, re.I):
            insert_after = re.search(r"setopt\s+INC_APPEND_HISTORY", text, re.I)
            if insert_after:
                pos = insert_after.end()
                text = text[:pos] + "\nsetopt SHARE_HISTORY" + text[pos:]
                changed = True
        if "HISTFILE" not in text:
            text = text.rstrip() + '\nexport HISTFILE="${HISTFILE:-$HOME/.zsh_history}"\n'
            changed = True
        if _MARKER not in text:
            text = re.sub(
                r"(# 历史记录[^\n]*\n)",
                rf"\1{_MARKER}\n",
                text,
                count=1,
            )
            changed = True
    else:
        block = "\n".join(_SNIPPET_LINES) + "\n"
        text = (text.rstrip() + "\n\n" + block) if text.strip() else block + "\n"
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return {"changed": changed, "path": str(path), **check_extended_history()}


def check_extended_history() -> dict:
    zshrc = _read_zshrc()
    hist_path = Path(os.path.expanduser("~/.zsh_history"))
    has_setopt = bool(re.search(r"setopt\s+EXTENDED_HISTORY", zshrc, re.I))
    has_histfile = "HISTFILE" in zshrc or hist_path.exists()
    sample = ""
    has_timestamps = False
    if hist_path.exists():
        try:
            sample = hist_path.read_text(encoding="utf-8", errors="replace")[:4000]
            has_timestamps = bool(re.search(r"^:\s*\d+:\d+;", sample, re.M))
        except OSError:
            pass
    ok = has_setopt
    snippet = "\n".join(_SNIPPET_LINES) + "\n"
    if has_setopt and not has_timestamps:
        detail = (
            "已在 ~/.zshrc 启用 EXTENDED_HISTORY；请新开终端或执行 source ~/.zshrc，"
            "之后的新命令会写入带时间戳的历史（旧记录无法补时间）。"
        )
    elif has_setopt:
        detail = "zsh 历史已带时间戳，行为补录可用。"
    else:
        detail = "建议在 ~/.zshrc 启用 EXTENDED_HISTORY，否则 shell 行为时间不准。"
    return {
        "ok": ok,
        "has_setopt": has_setopt,
        "has_timestamps": has_timestamps,
        "has_histfile": has_histfile,
        "snippet": snippet,
        "message": detail,
    }


def timestamp_stats(*, days: int = 7) -> dict:
    """zsh 历史中带 epoch 时间戳的行占比；用于洞察趋势与 shell check。"""
    from .collectors import shell as shell_col

    cfg = config.load_config()
    path = os.path.expanduser(cfg.get("shell_history") or "~/.zsh_history")
    entries = [(ts, cmd) for ts, cmd in shell_col._iter_history(path) if cmd.strip()]
    cutoff = db.now() - max(1, days) * 86400
    file_total = len(entries)
    file_with_ts = sum(1 for ts, _ in entries if ts is not None)
    window_commands = sum(1 for ts, _ in entries if ts is not None and ts >= cutoff)
    tail = entries[-500:] if len(entries) > 500 else entries
    tail_untimestamped = sum(1 for ts, _ in tail if ts is None)
    file_pct = round(file_with_ts / file_total * 100, 1) if file_total else 0.0
    cfg_check = check_extended_history()
    return {
        "days": days,
        "file_total": file_total,
        "file_with_ts": file_with_ts,
        "file_pct": file_pct,
        "window_commands": window_commands,
        "tail_untimestamped": tail_untimestamped,
        "config_ok": cfg_check.get("ok", False),
        "has_timestamps": cfg_check.get("has_timestamps", False),
        "snippet": cfg_check.get("snippet", ""),
        "message": cfg_check.get("message", ""),
    }
