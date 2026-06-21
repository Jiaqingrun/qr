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
LEGACY_CONDA_ENV = "kb"
LEGACY_CONDA_PATH_MARK = "/envs/kb/"
_CONDA_BASE = Path("/opt/anaconda3/envs")

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG: dict[str, Any] = {
    "ollama_url": "http://localhost:11434",
    "ollama_on_demand": False,
    "ollama_boot_timeout_seconds": 90,
    "ollama_flash_attention": True,
    "embed_model": "qwen3-embedding:8b",
    "chat_model": "qwen2.5:32b",
    "deep_model": "deepseek-r1:32b",
    "default_ask_model": "qwen2.5:32b",
    "ask_models": [
        {
            "id": "qwen2.5:32b",
            "label": "Qwen 2.5 · 32B",
            "hint": "默认推荐，日常查阅与总结",
            "reasoning": False,
            "default": True,
        },
        {
            "id": "deepseek-r1:32b",
            "label": "DeepSeek R1 · 32B",
            "hint": "深度推理，复杂架构与因果分析",
            "reasoning": True,
        },
    ],
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
    "web_watch_seconds": 45,
    "backfill_days": 365,
    "embed_dim": 4096,
    "scatter_roots": [
        "~", "~/Desktop", "~/Documents",
        "~/AndroidStudioProjects", "~/PyCharmMiscProject",
    ],
    "index_exclude_dirs": [
        ".git", "node_modules", ".venv", "venv", "env", "__pycache__",
        ".idea", ".gradle", "build", "dist", "Pods", ".next", "target",
        ".conda", ".mypy_cache", ".pytest_cache", "DerivedData", ".cache",
    ],
    # 文件名或路径片段；支持 * 通配。评测脚本不入库，避免 RAG 泄漏答案。
    "index_exclude_path_patterns": [
        "eval_suite.py",
        "model_eval.py",
        "model_compare_four.py",
        "**/tests/**",
        "**/test_*.py",
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
    "standards_revise_timeout_seconds": 1800,
    "prompt_guides_dir": "~/.qr/prompts",
    "prompt_guides_auto_sync": True,
    "prompt_guides_export_md": True,
    # 完成度 100%：末次修改距今至少 N 天（功能点全完成 + 优化完成 + 用户认可）
    "completion_dormant_days": 14,
    # 定时从 Cursor 对话摘要修订规范（默认随每周 qr update --summary week 执行）
    "standards_auto_revise": True,
    "standards_auto_on_weekly": True,
    "standards_auto_interval_hours": 168,
    "standards_auto_global": True,
    "standards_auto_projects": True,
    "standards_auto_max_projects": 2,
    "evolution_auto_sync": True,
    "evolution_sports_cursor_min": 5,
    "backup_keep_count": 10,
    "parent_expand_chars": 400,
    "code_aware_chunking": True,
    "rerank_enabled": True,
    "retrieval_vec_oversample": 8,
    "retrieval_max_per_path": 2,
    "cursor_precise_time": True,
    "session_auto_summary": True,
    "alert_dormant_days": 30,
    "alert_rag_eval_drop_pct": 10,
    "eval_monthly_day": 1,
    "eval_monthly_hour": 3,
    "retrieval_relation_expand": True,
    "retrieval_relation_max_projects": 2,
    "retrieval_relation_link_types": ["depends", "supports", "related", "co_dev"],
    "retrieval_relation_discount": 0.85,
    "index_incremental_after_ingest": True,
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


def git_roots(cfg: dict[str, Any] | None = None) -> list[Path]:
    """Git 采集扫描根（仅 git_scan_roots，不与 scatter_roots 合并）。"""
    cfg = cfg or load_config()
    key = "git_scan_roots" if cfg.get("git_scan_roots") else "index_roots"
    return expand_paths(cfg.get(key, []))


def ensure_dirs() -> None:
    migrate_legacy_home()
    for d in (
        QR_HOME,
        SUMMARIES_DIR,
        LOGS_DIR,
        QR_HOME / "notes",
        QR_HOME / "backups",
        QR_HOME / "cursor_chats",
    ):
        d.mkdir(parents=True, exist_ok=True)


def resolve_qr_argv() -> list[str]:
    """launchd 与安装脚本使用的可执行入口，优先 conda 环境 ``qr``（勿用旧名 ``kb``）。"""
    import sys

    qr_bin = _CONDA_BASE / "qr" / "bin" / "qr"
    if qr_bin.is_file():
        return [str(qr_bin)]
    qr_py = _CONDA_BASE / "qr" / "bin" / "python"
    if qr_py.is_file():
        return [str(qr_py), "-m", "qr.cli"]
    found = shutil.which("qr")
    if found and LEGACY_CONDA_PATH_MARK not in found:
        return [found]
    if found:
        pass
    candidate = Path(sys.executable).parent / "qr"
    if candidate.is_file():
        return [str(candidate)]
    return [found] if found else [str(qr_bin)]


def legacy_kb_findings() -> list[dict[str, str]]:
    """检测仍使用旧名 kb 的 conda 环境或 launchd 配置。"""
    out: list[dict[str, str]] = []
    if os.environ.get("CONDA_DEFAULT_ENV") == LEGACY_CONDA_ENV:
        out.append({
            "area": "conda",
            "level": "warn",
            "message": f"当前 shell 在 conda 环境「{LEGACY_CONDA_ENV}」中，知识库应使用环境「qr」",
            "fix": "conda activate qr（若未安装：conda create -n qr python=3.12 && pip install -e ~/QR/dev/qr）",
        })
    exe = shutil.which("qr") or ""
    if LEGACY_CONDA_PATH_MARK in exe:
        out.append({
            "area": "conda",
            "level": "warn",
            "message": f"PATH 中的 qr 命令仍来自 {LEGACY_CONDA_PATH_MARK}",
            "fix": "conda activate qr && pip install -e ~/QR/dev/qr",
        })
    agents = Path.home() / "Library" / "LaunchAgents"
    stale: list[str] = []
    if agents.is_dir():
        for plist in sorted(agents.glob("com.qr*.plist")):
            try:
                text = plist.read_text(encoding="utf-8")
            except OSError:
                continue
            if LEGACY_CONDA_PATH_MARK in text:
                stale.append(plist.name)
    if stale:
        out.append({
            "area": "schedule",
            "level": "warn",
            "message": "后台任务仍指向旧 conda 环境 kb: " + ", ".join(stale[:4]),
            "fix": "conda activate qr && qr schedule install && qr web --install",
        })
    kb_bin = _CONDA_BASE / LEGACY_CONDA_ENV / "bin" / "qr"
    if kb_bin.is_file() and (_CONDA_BASE / "qr").is_dir():
        out.append({
            "area": "conda",
            "level": "info",
            "message": f"检测到并存 conda 环境「{LEGACY_CONDA_ENV}」与「qr」，建议只保留 qr",
            "fix": f"确认 qr 环境可用后：conda remove -n {LEGACY_CONDA_ENV}",
        })
    return out
