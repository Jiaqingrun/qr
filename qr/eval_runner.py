"""模型评测执行：写入 ~/.qr/logs/model_eval.json 并保留时间戳快照。"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import config, eval_suite, timeutil


def eval_log_path(ts: int | None = None) -> Path:
    """当月评测 Markdown：~/.qr/logs/eval-YYYYMM.md"""
    ts = ts or int(time.time())
    ym = time.strftime("%Y%m", time.localtime(ts))
    return config.LOGS_DIR / f"eval-{ym}.md"


def render_eval_markdown(data: dict, *, generated_ts: int | None = None) -> str:
    """由 model_eval.json 结构生成月度评测 Markdown。"""
    ts = generated_ts or int(time.time())
    ym = time.strftime("%Y-%m", time.localtime(ts))
    rag = data.get("rag_summary") or {}
    chat = data.get("chat_model") or "—"
    deep = data.get("deep_model") or "—"
    lines = [
        f"# 模型评测 · {ym}",
        "",
        f"生成时间：{timeutil.format_local(ts)}",
        "",
        f"> 命令：`qr eval run` · JSON：`~/.qr/logs/model_eval.json`",
        "",
        "## 配置",
        "",
        f"- 日常问答：`{chat}`",
        f"- 深度推理：`{deep}`",
        "",
        "## 检索基线",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 命中率 | {rag.get('retrieval_rate', 0)}%（{rag.get('retrieval_ok', 0)}/{rag.get('cases', 0)}） |",
        f"| 考题泄漏 | {rag.get('forbidden_hits', 0)} 题 |",
        f"| 均检索耗时 | {rag.get('search_avg', 0)}s |",
        "",
    ]
    rag_rows = data.get("rag_baseline") or []
    bad = [r for r in rag_rows if not r.get("retrieval_ok") or r.get("retrieval_forbidden")]
    if bad:
        lines.extend(["### 未命中 / 泄漏", ""])
        for r in bad:
            flags = []
            if not r.get("retrieval_ok"):
                flags.append("未命中")
            if r.get("retrieval_forbidden"):
                flags.append("泄漏")
            lines.append(f"- `{r.get('case')}`: {', '.join(flags)}")
        lines.append("")

    pass_counts = eval_suite.model_pass_counts(data)
    total = eval_suite.eval_case_total(data)
    lines.extend(["## 模型必达通过率", ""])
    for name, n in sorted(pass_counts.items()):
        lines.append(f"- **{name}**：{n}/{total}")
    lines.append("")

    results = data.get("results") or {}
    for model_key in sorted(results.keys()):
        rows = results.get(model_key) or []
        if not isinstance(rows, list) or not rows:
            continue
        label = rows[0].get("model") or model_key
        lines.extend([f"## {model_key}（{label}）", ""])
        lines.extend([
            "| 用例 | tier | 必达 | 检索 | 生成(s) |",
            "|------|------|------|------|---------|",
        ])
        for r in rows:
            must = "✓" if r.get("must_pass") else "✗"
            retr = "✓" if r.get("retrieval_ok") else "✗"
            if r.get("retrieval_forbidden"):
                retr += " 泄漏"
            lines.append(
                f"| {r.get('case', '?')} | {r.get('tier', '')} | {must} | {retr} | {r.get('ask_s', '—')} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_eval_log_markdown(data: dict, *, generated_ts: int | None = None) -> Path:
    config.ensure_dirs()
    path = eval_log_path(generated_ts)
    path.write_text(render_eval_markdown(data, generated_ts=generated_ts), encoding="utf-8")
    return path


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
    md_path = ""
    if cur.is_file():
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        snap = config.LOGS_DIR / f"model_eval-{stamp}.json"
        snap.write_text(cur.read_text(encoding="utf-8"), encoding="utf-8")
        snap_path = str(snap)
        try:
            data = json.loads(cur.read_text(encoding="utf-8"))
            md_path = str(write_eval_log_markdown(data))
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "ok": True,
        "path": str(cur),
        "snapshot": snap_path,
        "markdown": md_path,
        "stdout": (proc.stdout or "")[-1200:],
    }
