from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

QR_HOME = Path(os.environ.get("QR_HOME", str(Path.home() / ".qr"))).expanduser()
DB_PATH = QR_HOME / "qr.db"
CONFIG_PATH = QR_HOME / "config.json"
STANDARDS_PATH = QR_HOME / "standards.md"
SUMMARIES_DIR = QR_HOME / "summaries"
LOGS_DIR = QR_HOME / "logs"

_LEGACY_HOME = Path.home() / ".kb"
_LEGACY_DB_NAME = "kb.db"

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG: dict[str, Any] = {
    "ollama_url": "http://localhost:11434",
    "embed_model": "bge-m3",
    "chat_model": "qwen2.5:32b",
    "deep_model": "deepseek-r1:32b",
    "search_engine": "baidu",
    "web_results": 5,
    "baidu_api_key": "",
    "workspace_root": "~/QR",
    "project_categories": ["dev", "mobile", "experiments", "tools", "archive"],
    "default_project_category": "dev",
    "index_roots": ["~/QR"],
    "git_scan_roots": ["~/QR"],
    "shell_history": "~/.zsh_history",
    "cursor_projects_dir": "~/.cursor/projects",
    "cursor_poll_seconds": 60,
    "backfill_days": 365,
    "embed_dim": 1024,
    "scatter_roots": [
        "~", "~/Desktop", "~/Documents",
        "~/AndroidStudioProjects", "~/PyCharmMiscProject",
    ],
    "index_exclude_dirs": [
        ".git", "node_modules", ".venv", "venv", "env", "__pycache__",
        ".idea", ".gradle", "build", "dist", "Pods", ".next", "target",
        ".conda", ".mypy_cache", ".pytest_cache", "DerivedData", ".cache",
    ],
    "index_extensions": [
        ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".swift",
        ".go", ".rs", ".c", ".cc", ".cpp", ".h", ".hpp", ".m", ".mm",
        ".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".ini",
        ".sh", ".zsh", ".sql", ".html", ".css", ".scss", ".vue", ".dart",
    ],
    "max_file_bytes": 500_000,
    "chunk_chars": 1200,
    "chunk_overlap": 150,
    "files_collect_cap": 3000,
    "web_host": "127.0.0.1",
    "web_port": 8765,
    "context_tokens": 32768,
    "deep_context_tokens": 131072,
}


def migrate_legacy_home() -> list[str]:
    """将 ~/.kb 数据迁移到 ~/.qr（仅迁移缺失项）。"""
    actions: list[str] = []
    if not _LEGACY_HOME.exists():
        return actions
    QR_HOME.mkdir(parents=True, exist_ok=True)
    for item in _LEGACY_HOME.iterdir():
        dest = QR_HOME / item.name
        if item.name == _LEGACY_DB_NAME:
            dest = QR_HOME / "qr.db"
            if dest.exists():
                continue
            shutil.copy2(item, dest)
            actions.append(f"已复制数据库 {_LEGACY_DB_NAME} → qr.db")
            continue
        if dest.exists():
            if item.is_dir() and dest.is_dir():
                for sub in item.rglob("*"):
                    if sub.is_file():
                        rel = sub.relative_to(item)
                        target = dest / rel
                        if not target.exists():
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(sub, target)
                actions.append(f"已合并目录 {item.name}")
            continue
        shutil.move(str(item), str(dest))
        actions.append(f"已迁移 {item.name}")
    try:
        if _LEGACY_HOME.exists() and not any(_LEGACY_HOME.iterdir()):
            _LEGACY_HOME.rmdir()
            actions.append("已删除空的 ~/.kb")
    except OSError:
        pass
    return actions


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p)).resolve() if "~" in p or p.startswith("/") else Path(p)


def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except json.JSONDecodeError:
            pass
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    QR_HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


def expand_paths(paths: list[str]) -> list[Path]:
    return [_expand(p) for p in paths]


def scan_roots(cfg: dict[str, Any] | None = None) -> list[Path]:
    cfg = cfg or load_config()
    seen: set[str] = set()
    roots: list[Path] = []
    for key in ("index_roots", "git_scan_roots", "scatter_roots"):
        for p in expand_paths(cfg.get(key, [])):
            key_s = str(p)
            if key_s in seen or not p.exists():
                continue
            seen.add(key_s)
            roots.append(p)
    return roots


def ensure_dirs() -> None:
    migrate_legacy_home()
    for d in (QR_HOME, SUMMARIES_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)
