"""设计者最小验收：doctor + 项目测试 + Web 点验提示。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from . import config, db, health, workspace

DECISION_TEMPLATE = (
    "qr log --type decision "
    '"问题：… | 选项：… | 结论：… | 原因：…"'
)


def resolve_project(
    *,
    project: str | None = None,
    cwd: Path | None = None,
) -> tuple[str, Path | None]:
    """解析 project_id 与项目目录。"""
    cfg = config.load_config()
    root = workspace.workspace_root(cfg)
    if project:
        pid = workspace.canonical_project_id(project.strip(), cfg) or project.strip()
        return pid, workspace.resolve_project_dir(pid, cfg)
    base = (cwd or Path.cwd()).resolve()
    pid = workspace.project_from_path(base, root)
    proj_dir = workspace.resolve_project_dir(pid, cfg)
    if proj_dir and proj_dir.is_dir():
        return pid, proj_dir
    try:
        base.relative_to(root.resolve())
        return pid, base if base.is_dir() else base.parent
    except ValueError:
        return pid, None


def _doctor_summary() -> dict[str, Any]:
    rep = health.diagnose()
    errors = [i for i in rep.get("issues", []) if i.get("level") == "error"]
    warns = [i for i in rep.get("issues", []) if i.get("level") == "warn"]
    return {
        "ok": rep.get("ok", False) and not errors,
        "has_error": bool(errors),
        "error_count": len(errors),
        "warn_count": len(warns),
        "ok_items": rep.get("ok_items", [])[:8],
        "issues": rep.get("issues", []),
    }


def _tests_dir(project_dir: Path | None, project_id: str) -> Path | None:
    if project_dir:
        td = project_dir / "tests"
        if td.is_dir():
            return td
    if project_id == workspace.TARGET_QR:
        repo_tests = config.REPO_ROOT / "tests"
        if repo_tests.is_dir():
            return repo_tests
    if project_dir and project_dir.resolve() == config.REPO_ROOT.resolve():
        repo_tests = config.REPO_ROOT / "tests"
        if repo_tests.is_dir():
            return repo_tests
    return None


def _run_unittest(tests_dir: Path, cwd: Path) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "ran": True,
            "message": "测试超时（>5 分钟）",
            "hint": "手动运行: python3 -m unittest discover -s tests",
        }
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-12:])
    if proc.returncode == 0:
        return {"ok": True, "ran": True, "message": "全部通过", "tail": tail}
    return {
        "ok": False,
        "ran": True,
        "message": "存在失败用例",
        "tail": tail,
        "hint": f"在项目目录执行: cd {cwd} && python3 -m unittest discover -s tests -v",
    }


def web_verify_url() -> str:
    cfg = config.load_config()
    host = cfg.get("web_host", "127.0.0.1")
    port = int(cfg.get("web_port", 8765))
    return f"http://{host}:{port}"


def run_ship_check(
    *,
    project: str | None = None,
    cwd: Path | None = None,
    skip_tests: bool = False,
) -> dict[str, Any]:
    """聚合设计者验收步骤，供 CLI 与 Web 复用。"""
    pid, proj_dir = resolve_project(project=project, cwd=cwd)
    steps: list[dict[str, Any]] = []

    doc = _doctor_summary()
    steps.append({
        "id": "doctor",
        "title": "检查系统是否正常",
        "ok": doc["ok"] and not doc["has_error"],
        "detail": (
            f"{len(doc.get('ok_items', []))} 项正常"
            + (f"，{doc['warn_count']} 项提示" if doc["warn_count"] else "")
            + (f"，{doc['error_count']} 项需处理" if doc["error_count"] else "")
        ),
        "issues": doc.get("issues", [])[:6],
        "ok_items": doc.get("ok_items", []),
    })

    test_result: dict[str, Any] = {"ok": True, "ran": False, "message": "无 tests/ 目录，已跳过"}
    if not skip_tests:
        td = _tests_dir(proj_dir, pid)
        if td:
            run_cwd = td.parent
            test_result = _run_unittest(td, run_cwd)
        elif pid == workspace.TARGET_QR or (proj_dir and proj_dir.resolve() == config.REPO_ROOT.resolve()):
            test_result = _run_unittest(config.REPO_ROOT / "tests", config.REPO_ROOT)
    steps.append({
        "id": "tests",
        "title": "自动测试",
        "ok": test_result.get("ok", True),
        "detail": test_result.get("message", ""),
        "ran": test_result.get("ran", False),
        "hint": test_result.get("hint"),
        "tail": test_result.get("tail"),
    })

    web_url = web_verify_url()
    steps.append({
        "id": "web",
        "title": "试用刚改的功能",
        "ok": True,
        "detail": f"在浏览器打开 {web_url} 点验相关页面",
        "url": web_url,
    })

    all_ok = all(s["ok"] for s in steps if s["id"] != "web")
    result = {
        "project": pid,
        "project_dir": str(proj_dir) if proj_dir else None,
        "ok": all_ok,
        "steps": steps,
        "decision_template": DECISION_TEMPLATE,
        "decision_hint": "记下为什么这样选",
    }

    db.init_db()
    with db.session() as conn:
        n = int(db.get_state(conn, "ship_check_count") or "0") + 1
        db.set_state(conn, "ship_check_count", str(n))
        db.set_state(conn, "ship_check_last_at", str(db.now()))
        db.set_state(conn, "ship_check_last_project", pid)
        db.set_state(conn, f"ship_check_at:{pid}", str(db.now()))

    return result


def exit_code(result: dict[str, Any]) -> int:
    if not result.get("ok"):
        return 1
    for step in result.get("steps", []):
        if step.get("id") == "doctor" and not step.get("ok"):
            return 1
        if step.get("id") == "tests" and step.get("ran") and not step.get("ok"):
            return 1
    return 0
