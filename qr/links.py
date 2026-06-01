from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import config


def allowed_roots() -> list[Path]:
    cfg = config.load_config()
    roots: list[Path] = []
    for key in ("index_roots", "git_scan_roots", "scatter_roots"):
        roots.extend(config.expand_paths(cfg.get(key, [])))
    roots.append(config.QR_HOME)
    out: list[Path] = []
    seen: set[str] = set()
    for r in roots:
        try:
            p = r.expanduser().resolve()
        except OSError:
            continue
        s = str(p)
        if s not in seen and p.exists():
            seen.add(s)
            out.append(p)
    return out


def path_allowed(path: Path) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return False
    if not resolved.exists():
        return False
    for root in allowed_roots():
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _git_repo(project: str | None) -> Path | None:
    if not project:
        return None
    for root in allowed_roots():
        for dirpath, dirnames, _ in os.walk(root):
            if ".git" in dirnames:
                repo = Path(dirpath)
                if repo.name == project:
                    return repo.resolve()
                dirnames[:] = []
            elif len(Path(dirpath).relative_to(root).parts) >= 4:
                dirnames[:] = []
    return None


def _first_git_file(content: str, repo: Path) -> Path | None:
    if "变更文件:" not in content:
        return None
    _, _, rest = content.partition("变更文件:\n")
    for line in rest.splitlines():
        if "\t" not in line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        rel = parts[2].strip()
        if not rel:
            continue
        full = (repo / rel).resolve()
        if full.exists() and path_allowed(full):
            return full
    return None


def event_link(source: str, title: str | None, content: str | None,
               project: str | None) -> dict | None:
    """解析事件可打开的目标路径。"""
    if source == "file" and content:
        p = Path(content).expanduser()
        if path_allowed(p):
            return {"path": str(p.resolve()), "label": title or p.name, "kind": "file"}

    if source == "git":
        repo = _git_repo(project)
        if repo is None:
            return None
        if content:
            f = _first_git_file(content, repo)
            if f:
                return {"path": str(f), "label": f.name, "kind": "file"}
        if path_allowed(repo):
            return {"path": str(repo), "label": project or repo.name, "kind": "dir"}

    return None


def open_path(path: str) -> None:
    p = Path(path).expanduser().resolve()
    if not path_allowed(p):
        raise PermissionError("路径不在允许范围内")
    subprocess.run(["open", str(p)], check=True)
