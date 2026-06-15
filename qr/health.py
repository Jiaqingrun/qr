from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

from . import config, db, permissions, scan_paths, shell_check

_SHELL_TS_RE = re.compile(r"^: \d+:\d+;")

_STATUS_CACHE: dict[str, Any] = {"ts": 0.0, "payload": None}
STATUS_CACHE_TTL = 45.0


def invalidate_status_cache() -> None:
    _STATUS_CACHE["ts"] = 0.0
    _STATUS_CACHE["payload"] = None


def _cursor_ts_coverage(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT meta FROM events WHERE source='cursor'"
    ).fetchall()
    total = len(rows)
    if not total:
        return {"total": 0, "estimated": 0, "exact": 0, "pct_exact": 100.0}
    estimated = 0
    for r in rows:
        meta = r["meta"]
        if not meta:
            estimated += 1
            continue
        try:
            if json.loads(meta).get("ts_estimated"):
                estimated += 1
        except json.JSONDecodeError:
            estimated += 1
    exact = total - estimated
    return {
        "total": total,
        "estimated": estimated,
        "exact": exact,
        "pct_exact": round(100.0 * exact / total, 1),
    }


def _shell_history_sample() -> dict:
    path = Path(os.path.expanduser(config.load_config()["shell_history"]))
    if not path.exists():
        return {"lines": 0, "with_ts": 0, "pct_ts": 0.0, "ok": False}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"lines": 0, "with_ts": 0, "pct_ts": 0.0, "ok": False, "error": str(e)}
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {"lines": 0, "with_ts": 0, "pct_ts": 0.0, "ok": False}
    sample = lines[-500:]
    with_ts = sum(1 for ln in sample if _SHELL_TS_RE.match(ln))
    pct = round(100.0 * with_ts / len(sample), 1)
    return {
        "lines": len(lines),
        "with_ts": with_ts,
        "sample": len(sample),
        "pct_ts": pct,
        "ok": pct >= 80.0,
    }


def _tracker_health() -> dict:
    with db.session() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM app_usage").fetchone()["c"]
        err = db.get_state(conn, "tracker_last_error")
        ok_at = db.get_state(conn, "tracker_last_ok")
    recent = False
    if ok_at:
        try:
            recent = db.now() - int(ok_at) < 3600
        except ValueError:
            pass
    sched = _schedule_loaded()
    agent_up = sched["agents"].get("com.qr.tracker", False)
    ok = (recent and not err) or (agent_up and n > 0 and not err)
    return {
        "sessions": n,
        "last_error": err,
        "recent_sample": recent,
        "agent_loaded": agent_up,
        "ok": ok,
    }


def _schedule_loaded() -> dict:
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    labels = [
        "com.qr.tracker", "com.qr.cursor", "com.qr.auto", "com.qr.eval",
        "com.qr.web", "com.qr.web-watch",
    ]
    loaded = {lb: lb in out for lb in labels}
    return {"agents": loaded, "ok": all(loaded.values())}


def diagnose(conn: sqlite3.Connection | None = None) -> dict:
    """汇总各子系统边界状态与修复建议。"""
    cfg = config.load_config()
    own_conn = conn is None
    if own_conn:
        db.init_db()
        conn = db.connect()
    try:
        issues: list[dict] = []
        ok_items: list[str] = []

        sh = shell_check.check_extended_history()
        hist = _shell_history_sample()
        if not sh["has_setopt"]:
            issues.append({
                "area": "shell",
                "level": "warn",
                "message": "zsh 未启用 EXTENDED_HISTORY",
                "fix": "qr shell enable && source ~/.zshrc",
            })
        elif not hist["ok"]:
            issues.append({
                "area": "shell",
                "level": "warn",
                "message": f"近期命令仅 {hist['pct_ts']}% 带时间戳（旧历史无法补）",
                "fix": "新开终端执行几条命令后再 qr ingest",
            })
        else:
            ok_items.append("Shell 历史时间戳正常")

        cov = _cursor_ts_coverage(conn)
        if cov["total"] and cov["pct_exact"] < 70:
            issues.append({
                "area": "cursor",
                "level": "info",
                "message": (
                    f"Cursor 提问 {cov['estimated']}/{cov['total']} 条为估算时间"
                    f"（对话无 <timestamp> 标签）"
                ),
                "fix": "运行 qr backfill --source cursor 从 state.vscdb 回填精确时间",
            })
        elif cov["total"]:
            ok_items.append(f"Cursor 时间 {cov['pct_exact']}% 为精确戳")

        tr = _tracker_health()
        if not tr["ok"]:
            issues.append({
                "area": "tracker",
                "level": "warn",
                "message": tr["last_error"] or (
                    "应用追踪近 1 小时无本进程采样（launchd 任务可能独立运行）"
                ),
                "fix": "qr track-once 测试；确认 qr schedule status 中 com.qr.tracker 运行中",
            })
        else:
            ok_items.append("应用追踪正常")

        probes = permissions.probe_access()
        failed = [p for p in probes if not p["ok"]]
        if failed:
            issues.append({
                "area": "privacy",
                "level": "warn",
                "message": "部分受保护路径不可访问: " + ", ".join(p["label"] for p in failed[:3]),
                "fix": "qr permissions open && qr permissions guide",
            })
        else:
            ok_items.append("系统隐私路径可读")

        for item in config.legacy_kb_findings():
            issues.append(item)

        sched = _schedule_loaded()
        if not sched["ok"]:
            missing = [k for k, v in sched["agents"].items() if not v]
            issues.append({
                "area": "schedule",
                "level": "warn",
                "message": "后台任务未全部加载: " + ", ".join(missing),
                "fix": "运维页安装定时任务，或 qr schedule install",
            })
        else:
            ok_items.append("launchd 后台任务已加载")

        git_n = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE source='git'"
        ).fetchone()["c"]
        index_paths = [str(p) for p in config.expand_paths(cfg.get("index_roots", []))]
        git_paths = [str(p) for p in config.git_roots(cfg)]
        missing_git = sorted(set(index_paths) - set(git_paths))
        if missing_git:
            sample = ", ".join(missing_git[:2])
            more = f" 等 {len(missing_git)} 个" if len(missing_git) > 2 else ""
            issues.append({
                "area": "git",
                "level": "info",
                "message": f"Git 扫描目录未覆盖部分索引根：{sample}{more}",
                "fix": "运维页「对齐 Git 扫描目录」，然后 qr ingest --source git",
            })
        elif git_n < 5 and index_paths:
            issues.append({
                "area": "git",
                "level": "info",
                "message": f"Git 事件仅 {git_n} 条，工作区内可能缺少 Git 仓库或尚未补录",
                "fix": "qr backfill --sources git 或确认项目目录含 .git",
            })
        elif git_n:
            ok_items.append(f"Git 事件 {git_n} 条")

        roots = config.expand_paths(cfg.get("index_roots", []))
        if any(scan_paths.is_home_root(r) for r in roots):
            ok_items.append("索引含主目录（已跳过 Library 等大目录）")

        try:
            from .ollama_client import Ollama, OllamaError
            Ollama().health()
            ok_items.append("Ollama 可用")
        except Exception as e:
            issues.append({
                "area": "ollama",
                "level": "error",
                "message": str(e),
                "fix": "启动 ollama 并拉取 config 中的 embed/chat 模型",
            })

        try:
            from . import prompt_guides

            prompt_guides.ensure_schema(conn)
            prompt_guides.stats(conn)
            ok_items.append("引导语模块可用")
        except Exception as exc:
            issues.append({
                "area": "web",
                "level": "warn",
                "message": f"引导语数据层异常: {exc}",
                "fix": "qr doctor；必要时 qr web --restart",
            })

        from . import governance, workspace

        governance.ensure_standards()
        std = governance.read_standards()
        if "## 六、" not in std:
            issues.append({
                "area": "standards",
                "level": "warn",
                "message": "全局规范缺少「第六章 界面与视觉」",
                "fix": "qr standards --restore 或重新打开 Web 规范页以自动合并模板章节",
            })
        else:
            ok_items.append("全局规范含界面与视觉章节")

        from . import project_standards

        missing_proj: list[str] = []
        mixed_proj: list[str] = []
        ws_projects = list(governance.iter_workspace_projects(cfg))
        for proj in ws_projects:
            pid = workspace.project_from_path(proj, workspace.workspace_root(cfg))
            body = project_standards.read_project_standards(proj)
            if not body:
                missing_proj.append(pid)
                continue
            if project_standards.mixed_standards_issues(body):
                mixed_proj.append(pid)
        if missing_proj:
            sample = ", ".join(missing_proj[:5])
            more = f" 等 {len(missing_proj)} 个" if len(missing_proj) > 5 else ""
            issues.append({
                "area": "standards",
                "level": "info",
                "message": f"部分工作区项目缺少 PROJECT.md：{sample}{more}",
                "fix": "qr project-standards <项目> --edit 或 qr workspace new 会自动创建模板",
            })
        elif ws_projects:
            ok_items.append("工作区项目均已具备 PROJECT.md")
        if mixed_proj:
            sample = ", ".join(mixed_proj[:5])
            more = f" 等 {len(mixed_proj)} 个" if len(mixed_proj) > 5 else ""
            issues.append({
                "area": "standards",
                "level": "warn",
                "message": f"PROJECT.md 混入全局规范（应分层、不混写）：{sample}{more}",
                "fix": "从 PROJECT.md 删除全局章节/条文，仅保留本项目约定；全局用 qr standards --edit",
            })

        cfg_auto = config.load_config()
        if cfg_auto.get("standards_auto_revise", True):
            with db.session() as auto_conn:
                last = db.get_state(auto_conn, "standards_auto_last_run")
            if last:
                try:
                    ago_h = (db.now() - int(last)) / 3600
                    ok_items.append(
                        f"规范定时修订已启用（上次 {ago_h:.0f} 小时前）"
                    )
                except ValueError:
                    ok_items.append("规范定时修订已启用")
            else:
                issues.append({
                    "area": "standards",
                    "level": "info",
                    "message": "尚未执行过定时规范修订",
                    "fix": "等待每周 com.qr.weekly，或 qr standards-auto --force",
                })

        from . import index_health

        idx = index_health.scan(conn)
        if idx.get("missing_files"):
            issues.append({
                "area": "index",
                "level": "warn",
                "message": (
                    f"索引中有 {idx['missing_files']} 个文档源文件已不存在"
                    f"（含 Cursor 转录 {idx.get('stale_cursor', 0)}）"
                ),
                "fix": "qr index-health --cleanup 或 Web 运维页清理孤儿索引",
            })
        elif idx.get("documents"):
            ok_items.append(f"索引文档 {idx['documents']} 个，路径均有效")
        for bi in idx.get("backup_issues", []):
            issues.append({
                "area": "backup",
                "level": "info",
                "message": bi,
                "fix": "qr backup 创建备份；qr backup --verify 校验",
            })

        from . import ui_audit

        ua = ui_audit.audit_ui(strict_api=True)
        if ua.get("ok"):
            ok_items.append(
                f"Web UI 自检通过（{ua.get('buttons', 0)} 按钮 · "
                f"{ua.get('search_inputs', 0)} 搜索框）"
            )
        else:
            for ui in ua.get("issues") or []:
                if ui.get("level") not in ("error", "warn"):
                    continue
                issues.append({
                    "area": "web_ui",
                    "level": ui["level"],
                    "message": ui.get("message", ""),
                    "fix": "检查 static/index.html 与 js/qr-features.js；qr web --restart",
                })

        return {
            "ok": not any(i["level"] == "error" for i in issues),
            "issues": issues,
            "ok_items": ok_items,
            "cursor": cov,
            "shell": hist,
            "tracker": tr,
            "schedule": sched,
            "config_path": str(config.CONFIG_PATH),
            "index_health": idx,
        }
    finally:
        if own_conn:
            conn.close()


def _pillar_status(levels: list[str]) -> str:
    if "error" in levels:
        return "error"
    if "warn" in levels:
        return "warn"
    return "ok"


def status_dashboard(conn: sqlite3.Connection, *, use_cache: bool = True) -> dict:
    """
    四象限系统状态（采集 / 索引 / 对话 / 运维），供 Web 侧栏 1×4 展示。
    默认缓存 45 秒，避免每次轮询跑完整体检。
    """
    if use_cache:
        age = time.time() - float(_STATUS_CACHE["ts"])
        cached = _STATUS_CACHE.get("payload")
        if cached and age < STATUS_CACHE_TTL:
            return dict(cached)

    from . import prompt_guides, query
    from .ollama_client import Ollama, OllamaError

    diag = diagnose(conn)
    ev = {
        r["source"]: int(r["c"])
        for r in conn.execute(
            "SELECT source, COUNT(*) c FROM events GROUP BY source",
        ).fetchall()
    }
    event_total = sum(ev.values())
    docs = int(conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"])
    chunks = int(conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"])
    summ = int(conn.execute("SELECT COUNT(*) c FROM summaries").fetchone()["c"])
    chats = int(conn.execute("SELECT COUNT(*) c FROM chat_sessions").fetchone()["c"])
    usage_n = int(conn.execute("SELECT COUNT(*) c FROM app_usage").fetchone()["c"])
    try:
        std_n = int(
            conn.execute("SELECT COUNT(*) c FROM standards_versions").fetchone()["c"],
        )
    except sqlite3.OperationalError:
        std_n = 0

    pg = prompt_guides.stats(conn)
    try:
        proj_n = len(query.workspace_list_projects(500).get("projects", []))
    except Exception:
        proj_n = 0

    backend = "sqlite-vec" if db.vec_available() else "numpy"
    try:
        ollama_tags = Ollama().health()
        ollama_ok = True
    except OllamaError:
        ollama_tags = []
        ollama_ok = False

    sched = diag.get("schedule") or {}
    agents = sched.get("agents") or {}
    sched_on = sum(1 for v in agents.values() if v)
    sched_total = len(agents) or 4

    capture_levels: list[str] = []
    if event_total == 0:
        capture_levels.append("warn")
    if diag.get("shell", {}).get("ok") is False:
        capture_levels.append("warn")
    if not capture_levels:
        capture_levels.append("ok")

    index_levels: list[str] = []
    if docs == 0:
        index_levels.append("warn")
    if chunks == 0 and docs > 0:
        index_levels.append("warn")
    if not index_levels:
        index_levels.append("ok")

    dialog_levels = ["ok"]
    if pg.get("inbox", 0) > 200:
        dialog_levels = ["warn"]

    ops_levels = [i.get("level", "warn") for i in diag.get("issues", [])]
    if not ollama_ok:
        ops_levels.append("error")
    if sched_on < sched_total:
        ops_levels.append("warn")
    if not ops_levels:
        ops_levels = ["ok"]

    pillars = [
        {
            "id": "capture",
            "title": "采集",
            "hint": "行为 · 笔记 · 时间线",
            "status": _pillar_status(capture_levels),
            "metric": event_total,
            "metric_label": "事件",
            "lines": [
                {"label": "Cursor", "value": ev.get("cursor", 0)},
                {"label": "Shell", "value": ev.get("shell", 0)},
                {"label": "Git", "value": ev.get("git", 0)},
                {"label": "笔记", "value": ev.get("note", 0)},
                {"label": "知识库", "value": ev.get("qr", 0)},
                {"label": "文件", "value": ev.get("file", 0)},
                {"label": "总结", "value": summ},
            ],
        },
        {
            "id": "index",
            "title": "索引",
            "hint": "检索 · 向量 · 项目",
            "status": _pillar_status(index_levels),
            "metric": docs,
            "metric_label": "文档",
            "lines": [
                {"label": "向量块", "value": chunks},
                {"label": "项目", "value": proj_n},
                {"label": "引擎", "value": backend},
            ],
        },
        {
            "id": "dialog",
            "title": "对话",
            "hint": "问答 · 引导语",
            "status": _pillar_status(dialog_levels),
            "metric": chats,
            "metric_label": "会话",
            "lines": [
                {"label": "收件箱", "value": pg.get("inbox", 0)},
                {"label": "引导语", "value": pg.get("guides", 0)},
                {"label": "类型", "value": pg.get("types", 0)},
            ],
        },
        {
            "id": "ops",
            "title": "运维",
            "hint": "模型 · 后台 · 健康",
            "status": _pillar_status(ops_levels),
            "metric": sched_on,
            "metric_label": f"任务/{sched_total}",
            "lines": [
                {"label": "追踪", "value": usage_n},
                {"label": "规范版", "value": std_n},
                {
                    "label": "Ollama",
                    "value": len(ollama_tags) if ollama_ok else "离线",
                },
            ],
        },
    ]

    issue_n = len(diag.get("issues", []))
    ok_n = len(diag.get("ok_items", []))
    if not diag.get("ok"):
        summary = f"{issue_n} 项待处理 · {ok_n} 项正常"
    elif issue_n:
        summary = f"{ok_n} 项正常 · {issue_n} 项提示"
    else:
        summary = f"运行正常 · {ok_n} 项检查通过"

    result = {
        "pillars": pillars,
        "summary": summary,
        "health_ok": diag["ok"],
        "health_issues": diag["issues"],
        "health_ok_items": diag.get("ok_items", []),
        "event_total": event_total,
        "events": ev,
        "documents": docs,
        "chunks": chunks,
        "summaries": summ,
        "chats": chats,
        "usage_sessions": usage_n,
        "projects": proj_n,
        "standards_versions": std_n,
        "schedule_loaded": sched_on,
        "schedule_total": sched_total,
        "ollama_ok": ollama_ok,
        "ollama_models": len(ollama_tags),
        "ollama_tags": ollama_tags,
        "backend": backend,
    }
    if use_cache:
        _STATUS_CACHE["payload"] = result
        _STATUS_CACHE["ts"] = time.time()
    return result
