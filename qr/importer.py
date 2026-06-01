from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import config

MARKERS = {
    ".git", "package.json", "requirements.txt", "pyproject.toml",
    "environment.yml", "build.gradle", "build.gradle.kts", "settings.gradle",
    "Cargo.toml", "go.mod", "pom.xml", "setup.py", "Package.swift",
    "CMakeLists.txt", "Makefile", "README.md", "pubspec.yaml",
}

SKIP_NAMES = {
    "Library", "Applications", "Movies", "Music", "Pictures", "Public",
    "Desktop", "Documents", "Downloads", "Templates", ".Trash", "Parallels",
    "Virtual Machines.localized", "Projects",
}


def _is_project(d: Path) -> bool:
    try:
        names = {p.name for p in d.iterdir()}
    except (OSError, PermissionError):
        return False
    return bool(names & MARKERS)


def discover() -> list[Path]:
    cfg = config.load_config()
    roots = [Path(os.path.expanduser(p)) for p in cfg["scatter_roots"]]
    from . import workspace

    projects_dir = workspace.workspace_root().resolve()
    found: dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            continue
        candidates = [root] if root != Path.home() else []
        try:
            candidates += [c for c in root.iterdir() if c.is_dir()]
        except (OSError, PermissionError):
            continue
        for c in candidates:
            if c.name.startswith(".") or c.name in SKIP_NAMES:
                continue
            try:
                rc = c.resolve()
            except OSError:
                continue
            if rc == projects_dir or projects_dir in rc.parents:
                continue
            if _is_project(c):
                found[str(rc)] = rc
    return sorted(found.values(), key=lambda p: str(p))


def add_to_index(paths: list[Path]) -> list[str]:
    cfg = config.load_config()
    existing = set(cfg["index_roots"])
    added = []
    for p in paths:
        s = str(p)
        if s not in existing:
            cfg["index_roots"].append(s)
            existing.add(s)
            added.append(s)
    if added:
        config.save_config(cfg)
    return added


def move_to_projects(paths: list[Path], category: str | None = None) -> list[tuple[str, str]]:
    from . import workspace

    rows = workspace.migrate_paths(paths, category=category, dry_run=False)
    return [(r["src"], r["dest"]) for r in rows if r.get("status") == "moved"]
