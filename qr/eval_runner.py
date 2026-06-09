"""模型评测执行：写入 ~/.qr/logs/model_eval.json 并保留时间戳快照。"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import config


def _python() -> str:
    py = sys.executable
    if Path(py).exists():
        return py
    return shutil.which("python3") or py


def run_model_eval(*, timeout: int = 1800) -> dict[str, Any]:
    """运行 scripts/model_eval.py，结果写入 ~/.qr/logs/。"""
    config.ensure_dirs()
    script = config.REPO_ROOT / "scripts" / "model_eval.py"
    if not script.is_file():
        return {"ok": False, "error": f"评测脚本不存在: {script}"}

    py = _python()
    try:
        proc = subprocess.run(
            [py, str(script)],
            cwd=str(config.REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"评测超时（{timeout // 60} 分钟）"}
    except FileNotFoundError:
        return {"ok": False, "error": f"找不到 Python 解释器: {py}"}

    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()[-1200:]
        return {"ok": False, "error": f"评测失败: {msg}", "stdout": (proc.stdout or "")[-800:]}

    cur = config.LOGS_DIR / "model_eval.json"
    snap_path = ""
    if cur.is_file():
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        snap = config.LOGS_DIR / f"model_eval-{stamp}.json"
        snap.write_text(cur.read_text(encoding="utf-8"), encoding="utf-8")
        snap_path = str(snap)

    return {
        "ok": True,
        "path": str(cur),
        "snapshot": snap_path,
        "stdout": (proc.stdout or "")[-1200:],
    }
