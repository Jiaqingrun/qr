from __future__ import annotations

import os
import re
from pathlib import Path


def _read_zshrc() -> str:
    path = Path.home() / ".zshrc"
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


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
    ok = has_setopt or has_timestamps
    snippet = (
        "# QR本地知识库：启用带时间戳的 zsh 历史（便于行为补录）\n"
        "setopt EXTENDED_HISTORY\n"
        "setopt INC_APPEND_HISTORY\n"
        "setopt SHARE_HISTORY\n"
    )
    return {
        "ok": ok,
        "has_setopt": has_setopt,
        "has_timestamps": has_timestamps,
        "has_histfile": has_histfile,
        "snippet": snippet,
        "message": (
            "zsh 历史已带时间戳，行为补录可用。"
            if ok
            else "建议在 ~/.zshrc 启用 EXTENDED_HISTORY，否则 shell 行为时间不准、无法准确 backfill。"
        ),
    }
