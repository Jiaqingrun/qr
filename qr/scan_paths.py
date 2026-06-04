from __future__ import annotations

import os
from pathlib import Path

# 扫描用户主目录时跳过的大型/系统目录（行为采集与索引共用）
HOME_ROOT_SKIP = frozenset({
    "Library",
    "Pictures",
    "Movies",
    "Music",
    "Applications",
    ".Trash",
    ".cursor",
    ".npm",
    ".cargo",
})


def is_home_root(path: Path) -> bool:
    try:
        return path.resolve() == Path.home().resolve()
    except OSError:
        return False


def prune_walk_dirnames(dirnames: list[str], parent: Path) -> None:
    if is_home_root(parent):
        dirnames[:] = [
            d for d in dirnames
            if d not in HOME_ROOT_SKIP and not d.startswith(".")
        ]
