from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

KB_HOME = Path(os.environ.get("KB_HOME", str(Path.home() / ".kb"))).expanduser()
DB_PATH = KB_HOME / "kb.db"
CONFIG_PATH = KB_HOME / "config.json"
STANDARDS_PATH = KB_HOME / "standards.md"
SUMMARIES_DIR = KB_HOME / "summaries"
LOGS_DIR = KB_HOME / "logs"

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG: dict[str, Any] = {
    "ollama_url": "http://localhost:11434",
    "embed_model": "nomic-embed-text",
    "chat_model": "deepseek-r1:32b",
    "index_roots": ["~/Projects"],
    "git_scan_roots": ["~/Projects"],
    "shell_history": "~/.zsh_history",
    "cursor_projects_dir": "~/.cursor/projects",
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
}


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
    KB_HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


def expand_paths(paths: list[str]) -> list[Path]:
    return [_expand(p) for p in paths]


def ensure_dirs() -> None:
    for d in (KB_HOME, SUMMARIES_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)
