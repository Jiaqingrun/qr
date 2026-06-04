from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config, shell_check

# macOS 系统设置 → 隐私与安全性（Sonoma+）
PRIVACY_URLS = {
    "full_disk": (
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
        "?Privacy_AllFiles"
    ),
    "accessibility": (
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
        "?Privacy_Accessibility"
    ),
    "automation": (
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
        "?Privacy_Automation"
    ),
    "files": (
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
        "?Privacy_FilesAndFolders"
    ),
}

FULL_SCOPE_EXTRA_EXCLUDES = [
    "Library/Caches",
    "Library/Logs",
    "Library/Group Containers",
    "Library/Containers",
    "Pictures",
    "Movies",
    "Music",
    ".npm",
    ".cargo",
    ".rustup",
    ".Trash",
    "node_modules",
]

FULL_SCOPE_PATCH: dict = {
    "permission_scope": "full",
    "index_roots": ["~/QR", "~"],
    "git_scan_roots": ["~"],
    "scatter_roots": [
        "~",
        "~/QR",
        "~/Desktop",
        "~/Documents",
        "~/Downloads",
        "~/Projects",
        "~/AndroidStudioProjects",
        "~/PyCharmMiscProject",
        "~/.cursor",
    ],
    "files_collect_cap": 12_000,
}


def trusted_executables() -> list[dict[str, str]]:
    """需在「系统设置」里手动勾选的程序路径。"""
    home = Path.home()
    items: list[dict[str, str]] = []
    py = Path(sys.executable).resolve()
    items.append({"role": "Python（launchd / qr 命令实际进程）", "path": str(py)})
    qr_bin = shutil.which("qr")
    if qr_bin:
        items.append({"role": "qr 命令", "path": str(Path(qr_bin).resolve())})
    for name in ("Terminal.app", "iTerm.app", "Cursor.app", "Code.app"):
        p = Path("/Applications") / name
        if p.exists():
            items.append({"role": f"终端/编辑器 · {name}", "path": str(p)})
    app = home / "Applications" / "QR本地知识库.app"
    built = config.REPO_ROOT / "packaging/macos/build/QR本地知识库.app"
    for p in (app, built):
        if p.exists():
            items.append({"role": "QR 桌面启动器", "path": str(p.resolve())})
            break
    zsh = shutil.which("zsh") or "/bin/zsh"
    items.append({"role": "zsh（读取历史）", "path": zsh})
    return items


def probe_access() -> list[dict]:
    """探测受 TCC 保护路径是否可读（需已授予完全磁盘访问等）。"""
    home = Path.home()
    checks = [
        ("用户主目录", home),
        ("Cursor 对话", home / ".cursor/projects"),
        ("zsh 历史", home / ".zsh_history"),
        ("QR 工作区", home / "QR"),
        ("邮件数据", home / "Library/Mail"),
        ("Safari 数据", home / "Library/Safari"),
        ("应用支持", home / "Library/Application Support"),
    ]
    out: list[dict] = []
    for label, path in checks:
        ok, detail = False, "不存在"
        if path.exists():
            try:
                if path.is_file():
                    with open(path, "rb"):
                        pass
                    ok = True
                    detail = "可读"
                else:
                    next(path.iterdir())
                    ok = True
                    detail = "可列举"
            except PermissionError:
                detail = "权限不足（需在系统设置授权）"
            except OSError as e:
                detail = str(e)
        out.append({"label": label, "path": str(path), "ok": ok, "detail": detail})
    out.append(_probe_automation())
    return out


def _probe_automation() -> dict:
    try:
        r = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get name of first '
                "application process whose frontmost is true",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        ok = r.returncode == 0 and bool(r.stdout.strip())
        detail = r.stdout.strip() if ok else (r.stderr.strip() or "未授权自动化")
    except Exception as e:
        ok, detail = False, str(e)
    return {
        "label": "自动化 · System Events（应用追踪）",
        "path": "osascript → System Events",
        "ok": ok,
        "detail": detail,
    }


def open_privacy_pane(name: str) -> bool:
    url = PRIVACY_URLS.get(name)
    if not url:
        return False
    subprocess.run(["open", url], check=False)
    return True


def open_all_privacy_panes() -> list[str]:
    opened = []
    for key in ("full_disk", "accessibility", "automation", "files"):
        if open_privacy_pane(key):
            opened.append(key)
    return opened


def apply_full_scope_config() -> dict:
    """扩大索引/采集范围（不替代系统隐私授权）。"""
    cfg = config.load_config()
    changed: list[str] = []
    for k, v in FULL_SCOPE_PATCH.items():
        if cfg.get(k) != v:
            cfg[k] = v
            changed.append(k)
    excl = list(cfg.get("index_exclude_dirs") or [])
    for d in FULL_SCOPE_EXTRA_EXCLUDES:
        if d not in excl:
            excl.append(d)
    if excl != cfg.get("index_exclude_dirs"):
        cfg["index_exclude_dirs"] = excl
        changed.append("index_exclude_dirs")
    config.save_config(cfg)
    return {"changed": changed, "config_path": str(config.CONFIG_PATH)}


def setup_summary() -> str:
    return (
        "macOS 不允许程序自行授予隐私权限。请按 `qr permissions guide` 列出的路径，"
        "在已打开的「隐私与安全性」各页中点击 + 添加并勾选；完成后运行 `qr permissions check`。"
    )
