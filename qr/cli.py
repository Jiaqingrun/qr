from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from . import (
    alerts,
    collectors,
    config,
    db,
    facts,
    governance,
    importer,
    indexer,
    project_panel,
    query,
    summary,
    tracker,
    usage,
    backfill,
)
from . import compliance, digest, export, health, permissions, shell_check, workspace
from .collectors import cursor
from .collectors import notes
from . import service_watch
from .ollama_client import Ollama, OllamaError

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="QR本地个人行为知识库与治理系统（离线，基于 ollama）")
console = Console()

help_app = typer.Typer(help="按主题查看常用命令")
app.add_typer(help_app, name="help")


@help_app.command("topics")
def help_topics():
    """Web/CLI 分组速查（简化导航对照）。"""
    console.print("[bold]五常（日常）[/bold]")
    for line in (
        "  qr web          打开 Web 控制台（http://127.0.0.1:8765）",
        "  qr ask \"…\"      向自己的代码提问",
        "  qr log \"…\"      记决策 / 进展",
        "  qr update       采集 + 索引",
        "  qr doctor       系统自检",
    ):
        console.print(line)
    console.print("\n[bold]主题[/bold]")
    groups = (
        ("问 / 检索", "ask · query · symbol · eval rag"),
        ("记录", "log · summary · timeline · prompts"),
        ("项目 / 工作区", "project · workspace · index"),
        ("设 / 运维", "web --restart · backup · schedule · standards"),
    )
    for title, cmds in groups:
        console.print(f"  [cyan]{title}[/cyan]  {cmds}")
    console.print("\n[dim]Web 日常档：侧栏 今日 / 问 / 记录 / 项目 / 提示库 / 设 · Cmd+K 命令面板[/dim]")


class _ConsoleTee:
    """镜像 stdout/stderr 到 console_log，供 Web 终端标签页展示。"""

    def __init__(self, stream, kind: str, job_id: str):
        self._stream = stream
        self._kind = kind
        self._job_id = job_id

    def write(self, data: str) -> int:
        if not data:
            return 0
        self._stream.write(data)
        if hasattr(self._stream, "flush"):
            self._stream.flush()
        from . import console_log

        text = console_log.strip_ansi(data).rstrip("\n")
        if text:
            console_log.emit(
                source="cli",
                kind=self._kind,
                job_id=self._job_id,
                text=text,
            )
        return len(data)

    def flush(self) -> None:
        if hasattr(self._stream, "flush"):
            self._stream.flush()

    def fileno(self) -> int:
        return self._stream.fileno()

    def isatty(self) -> bool:
        fn = getattr(self._stream, "isatty", None)
        return fn() if callable(fn) else False

    @property
    def encoding(self):
        return getattr(self._stream, "encoding", "utf-8")

    @property
    def errors(self):
        return getattr(self._stream, "errors", "strict")

ALL_SOURCES = ["shell", "git", "files", "cursor", "notes"]

shell_app = typer.Typer(help="Shell 历史采集配置")
app.add_typer(shell_app, name="shell")

perm_app = typer.Typer(help="系统权限与采集范围（macOS 需在系统设置中手动授权）")
app.add_typer(perm_app, name="permissions")


@perm_app.command("guide")
def permissions_guide():
    """列出需加入系统隐私白名单的程序路径。"""
    console.print("[bold]请在「系统设置 → 隐私与安全性」中添加并勾选以下程序：[/]\n")
    t = Table(title="建议授权的程序")
    t.add_column("用途")
    t.add_column("路径")
    for row in permissions.trusted_executables():
        t.add_row(row["role"], row["path"])
    console.print(t)
    console.print(
        "\n[bold]各页面建议：[/]\n"
        "· [cyan]完全磁盘访问[/]：Python、Terminal/Cursor、QR 桌面应用（读取 Mail/Safari/库目录等）\n"
        "· [cyan]辅助功能[/]：同上 Python（应用时长追踪 PyObjC 回退）\n"
        "· [cyan]自动化[/]：Python 或 Terminal → 允许控制「System Events」\n"
        "· [cyan]文件与文件夹[/]：按需勾选主目录、文稿、下载\n"
    )
    console.print(f"[dim]{permissions.setup_summary()}[/]")


@perm_app.command("open")
def permissions_open(
    pane: str = typer.Option(
        "all",
        "--pane",
        help="all | full_disk | accessibility | automation | files",
    ),
):
    """打开 macOS 隐私设置对应页面。"""
    if pane == "all":
        opened = permissions.open_all_privacy_panes()
        console.print(f"[green]✓[/] 已打开: {', '.join(opened)}")
    elif permissions.open_privacy_pane(pane):
        console.print(f"[green]✓[/] 已打开: {pane}")
    else:
        console.print(f"[red]未知页面[/] {pane}"); raise typer.Exit(1)


@perm_app.command("check")
def permissions_check():
    """检测关键路径与自动化是否已授权。"""
    rows = permissions.probe_access()
    t = Table(title="权限探测")
    t.add_column("项目")
    t.add_column("状态")
    t.add_column("说明")
    for r in rows:
        st = "[green]✓[/]" if r["ok"] else "[yellow]✗[/]"
        t.add_row(r["label"], st, r["detail"][:80])
    console.print(t)
    if not all(r["ok"] for r in rows):
        console.print("[dim]运行 qr permissions open 打开系统设置，再 qr permissions guide 查看要添加的程序[/]")


@perm_app.command("apply")
def permissions_apply():
    """将知识库采集范围设为「全用户目录」（仍须系统隐私授权）。"""
    r = permissions.apply_full_scope_config()
    if r["changed"]:
        console.print(f"[green]✓[/] 已更新 {config.CONFIG_PATH}")
        console.print(f"[dim]变更: {', '.join(r['changed'])}[/]")
    else:
        console.print("[green]✓[/] 采集范围已是全量配置")
    console.print(
        f"[dim]索引根: {', '.join(config.load_config().get('index_roots', []))}[/]"
    )


@perm_app.command("setup")
def permissions_setup(
    install_schedule: bool = typer.Option(True, "--schedule/--no-schedule"),
):
    """一键：扩大采集范围 + shell 历史 + 打开隐私设置 + 安装后台任务。"""
    permissions.apply_full_scope_config()
    shell_check.enable_extended_history()
    permissions.open_all_privacy_panes()
    console.print("[green]✓[/] 已写入全量采集配置并打开隐私设置页面")
    permissions_guide()
    if install_schedule:
        schedule("install")
    console.print(f"\n[bold yellow]请在本机系统设置中完成勾选后执行：[/]")
    console.print("  qr permissions check && qr ingest && qr index --reindex")


@shell_app.command("enable")
def shell_enable():
    """在 ~/.zshrc 启用 EXTENDED_HISTORY（带 epoch 的历史行）。"""
    r = shell_check.enable_extended_history()
    if r["changed"]:
        console.print(f"[green]✓[/] 已写入 {r['path']}")
    else:
        console.print(f"[green]✓[/] {r['path']} 已包含所需配置")
    console.print(f"[dim]{r['message']}[/]")
    if not r["has_timestamps"]:
        console.print("[dim]验证：新开终端后执行一条命令，再运行 qr ingest[/]")


@shell_app.command("check")
def shell_check_cmd(
    days: int = typer.Option(7, "--days", help="统计近 N 天带时间戳命令"),
    copy_snippet: bool = typer.Option(False, "--copy-snippet", help="仅输出可粘贴的 zshrc 配置"),
):
    """检查 zsh 历史是否适合 QR 行为补录。"""
    r = shell_check.check_extended_history()
    st = shell_check.timestamp_stats(days=days)
    if copy_snippet:
        console.print(r["snippet"])
        return
    if r["ok"]:
        console.print(f"[green]✓[/] {r['message']}")
    else:
        console.print(f"[yellow]![/] {r['message']}")
        console.print("[dim]运行 qr shell enable 可自动写入配置[/]")
    console.print(
        f"[dim]历史行带时间戳: {st['file_pct']}% "
        f"({st['file_with_ts']}/{st['file_total']}) · "
        f"近 {st['days']} 天可定位命令 {st['window_commands']} 条[/]"
    )
    if st["tail_untimestamped"]:
        console.print(
            f"[dim]最近 {min(500, st['file_total'])} 行中 "
            f"{st['tail_untimestamped']} 行无时间戳（启用 EXTENDED_HISTORY 后新命令会改善）[/]"
        )
    if not r["ok"]:
        console.print("\n[bold]可复制到 ~/.zshrc：[/]")
        console.print(r["snippet"])


@app.command()
def init():
    """初始化数据库、配置与个人规范。"""
    migrated = config.migrate_legacy_home()
    db.init_db()
    cfg = config.load_config()
    workspace.ensure_workspace_layout(cfg)
    config.save_config(cfg)
    sp = governance.ensure_standards()
    for msg in migrated:
        console.print(f"[green]✓[/] {msg}")
    console.print(f"[green]✓[/] 数据目录: {config.QR_HOME}")
    console.print(f"[green]✓[/] 数据库: {config.DB_PATH}")
    console.print(f"[green]✓[/] 配置: {config.CONFIG_PATH}")
    console.print(f"[green]✓[/] 个人规范: {sp}")
    try:
        with Ollama() as ol:
            models = ol.health()
        console.print(f"[green]✓[/] ollama 可用，模型: {', '.join(models)}")
    except OllamaError as e:
        console.print(f"[yellow]![/] {e}")
    sh = shell_check.enable_extended_history()
    if sh["changed"]:
        console.print(f"[green]✓[/] 已配置 zsh 扩展历史")
    console.print(f"[dim]{sh['message']}[/]")
    (config.QR_HOME / "notes").mkdir(parents=True, exist_ok=True)
    console.print(f"[green]✓[/] 笔记目录: {config.QR_HOME / 'notes'}（可放 *.md，ingest 自动同步）")
    for item in config.legacy_kb_findings():
        lvl = item.get("level", "warn")
        if lvl == "info":
            console.print(f"[dim]· {item['message']}[/]")
            console.print(f"[dim]  {item['fix']}[/]")
        else:
            console.print(f"[yellow]![/] {item['message']}")
            console.print(f"[dim]  → {item['fix']}[/]")


@app.command()
def doctor(
    fix: bool = typer.Option(
        False, "--fix", help="全量自检并清理：无效索引/向量块、幽灵项目、沿革噪声、同步稳定事实",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅统计将清理项，不写入"),
    json_out: bool = typer.Option(False, "--json", help="输出 JSON（供脚本/工具解析）"),
):
    """检查各子系统边界状态；--fix 时自动清理可修复项。"""
    from . import maintenance

    db.init_db()
    if fix or dry_run:
        console.print("[bold]全量自检 + 自动清理[/]" if fix else "[bold]全量自检（预览）[/]")
        if dry_run:
            from . import index_health as ih, workspace as ws

            with db.session() as conn:
                rep = ih.scan(conn)
                vec = ih.scan_vectors(conn)
                junk = ws.list_junk_project_ids()
                orphans = ih.cleanup_orphans(conn, dry_run=True)
                stale = ih.cleanup_stale_vectors(conn, dry_run=True)
                wrong = ih.cleanup_wrong_dim_chunks(conn, dry_run=True)
            console.print(
                f"失效文档 {rep['missing_files']} · 孤儿向量 {vec['stale_vec_rows']} · "
                f"维度异常块 {vec['wrong_dim_chunks']} · 幽灵项目 {len(junk)}"
            )
            if junk:
                for pid in junk:
                    console.print(f"  [dim]· 项目 {pid}[/]")
            console.print(
                f"[dim]将清理: 文档 {orphans['documents_removed']} · 块 {orphans['chunks_removed']} · "
                f"向量 {stale['vec_removed']} · 异常维文档 {wrong['documents_removed']}[/]"
            )
        else:
            full = maintenance.run_full_maintenance(fix=True)
            steps = full.get("steps") or {}
            for key, val in steps.items():
                if isinstance(val, dict):
                    parts = ", ".join(f"{k}={v}" for k, v in val.items() if v)
                    if parts:
                        console.print(f"[green]✓[/] {key}: {parts}")
                elif isinstance(val, list) and val:
                    console.print(f"[green]✓[/] {key}: {', '.join(str(x) for x in val)}")
            wp = steps.get("workspace_prune") or {}
            for pid in wp.get("pruned") or []:
                console.print(f"[green]✓[/] 已清理幽灵项目 {pid}")
            for err in wp.get("errors") or []:
                console.print(f"[yellow]![/] {err.get('project')}: {err.get('error')}")
            from . import ui_audit

            ua = full.get("ui_audit") or {}
            if ua.get("ok"):
                console.print(
                    f"[green]✓[/] Web UI: {ua.get('buttons', 0)} 按钮 · "
                    f"{ua.get('search_inputs', 0)} 搜索框绑定正常"
                )
            else:
                for line in ui_audit.format_issues(ua.get("issues") or [], limit=8):
                    console.print(f"[yellow]![/] {line}")
        console.print()

    rep = health.diagnose()
    if json_out:
        payload = {
            "ok": rep["ok"],
            "issue_count": len(rep["issues"]),
            "warn_count": sum(1 for i in rep["issues"] if i["level"] == "warn"),
            "error_count": sum(1 for i in rep["issues"] if i["level"] == "error"),
            "issues": rep["issues"],
            "ok_items": rep["ok_items"],
        }
        console.print(json.dumps(payload, ensure_ascii=False))
        if any(i["level"] == "error" for i in rep["issues"]):
            raise typer.Exit(1)
        return
    if rep["ok_items"]:
        for line in rep["ok_items"]:
            console.print(f"[green]✓[/] {line}")
    if rep["issues"]:
        t = Table(title="待完善项")
        t.add_column("模块")
        t.add_column("级别")
        t.add_column("说明")
        t.add_column("建议")
        for i in rep["issues"]:
            color = {"error": "red", "warn": "yellow", "info": "dim"}.get(i["level"], "white")
            t.add_row(i["area"], f"[{color}]{i['level']}[/]", i["message"], i["fix"])
        console.print(t)
    else:
        console.print("[green]✓[/] 未发现待处理项")
    if any(i["level"] == "error" for i in rep["issues"]):
        raise typer.Exit(1)


@app.command("ship-check")
def ship_check_cmd(
    project: str = typer.Option("", "-p", "--project", help="项目 id，如 dev/qr"),
    skip_tests: bool = typer.Option(False, "--skip-tests", help="跳过 unittest"),
):
    """设计者最小验收：doctor → 测试 → Web 点验提示。"""
    from . import ship_check

    db.init_db()
    pid = project.strip() or None
    result = ship_check.run_ship_check(project=pid, skip_tests=skip_tests)
    console.print(f"[bold]设计者验收 · {result['project']}[/]\n")
    for step in result.get("steps", []):
        mark = "[green]✓[/]" if step.get("ok") else "[red]✗[/]"
        console.print(f"{mark} {step.get('title')}: {step.get('detail', '')}")
        if step.get("id") == "doctor":
            for line in step.get("ok_items") or []:
                console.print(f"  [dim]· {line}[/]")
            for issue in step.get("issues") or []:
                if issue.get("level") == "error":
                    console.print(f"  [red]! {issue.get('message')}[/]")
                elif issue.get("level") == "warn":
                    console.print(f"  [yellow]! {issue.get('message')}[/]")
        if step.get("tail"):
            console.print(f"[dim]{step['tail']}[/]")
        if step.get("hint"):
            console.print(f"[dim]  → {step['hint']}[/]")
        if step.get("url"):
            console.print(f"  [cyan]{step['url']}[/]")
    console.print(
        f"\n[bold]下一步[/]：{result.get('decision_hint')} — "
        f"[dim]{result.get('decision_template')}[/]"
    )
    raise typer.Exit(ship_check.exit_code(result))


decision_app = typer.Typer(help="里程碑决策草稿")
app.add_typer(decision_app, name="decision")


@decision_app.command("draft")
def decision_draft_cmd(
    project: str = typer.Option("", "-p", "--project", help="项目 id"),
    session: str = typer.Option("", "--session", help="Cursor 会话 uuid"),
    turns: int = typer.Option(30, "--turns", help="纳入问话轮数"),
    save: bool = typer.Option(False, "--save", help="写入 ~/.qr/notes 供 ingest"),
):
    """从近 N 轮 Cursor 对话 + Git 摘要生成决策草稿（不自动入库）。"""
    from . import decision_draft

    db.init_db()
    draft = decision_draft.build_draft(
        session_id=session.strip() or None,
        project=project.strip() or None,
        turn_limit=turns,
    )
    console.print(Markdown(draft["text"]))
    if save:
        config.ensure_dirs()
        notes_dir = config.QR_HOME / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        sid = (draft.get("session_id") or "manual")[:8]
        path = notes_dir / f"decision-draft-{sid}.md"
        path.write_text(draft["text"], encoding="utf-8")
        console.print(f"[green]✓[/] 已写入 {path}（执行 qr ingest --source notes 同步）")


@app.command(name="session-checkpoint")
def session_checkpoint_cmd(
    session_id: str = typer.Argument("", help="Cursor 会话 uuid（与 --list 二选一）"),
    force: bool = typer.Option(False, "--force", help="覆盖已有 checkpoint"),
    list_long: bool = typer.Option(False, "--list", help="列出超过阈值的长会话"),
    limit: int = typer.Option(20, "--limit", help="--list 时最多显示条数"),
):
    """长 Cursor 会话 checkpoint（已完成 / 待办 / 风险）。"""
    from . import session_checkpoint
    from .ollama_client import OllamaError

    db.init_db()
    if list_long:
        with db.session() as conn:
            rows = session_checkpoint.list_long_sessions(conn, limit=limit)
        if not rows:
            console.print(f"[dim]无 ≥{session_checkpoint.min_turns()} 轮会话[/]")
            raise typer.Exit(0)
        t = Table(title=f"长会话（≥{session_checkpoint.min_turns()} 轮）")
        t.add_column("session_id")
        t.add_column("轮次", justify="right")
        t.add_column("项目")
        t.add_column("checkpoint")
        for r in rows:
            t.add_row(
                r["session_id"][:36],
                str(r["turns"]),
                r.get("project") or "—",
                "✓" if r.get("has_checkpoint") else "—",
            )
        console.print(t)
        console.print("[dim]生成：qr session-checkpoint <uuid>[/]")
        raise typer.Exit(0)
    if not session_id.strip():
        console.print("[red]✗[/] 请提供 session_id 或使用 --list")
        raise typer.Exit(1)
    try:
        with db.session() as conn:
            result = session_checkpoint.create_checkpoint(
                conn, session_id.strip(), force=force,
            )
    except (ValueError, OllamaError) as e:
        console.print(f"[red]✗[/] {e}")
        raise typer.Exit(1)
    if result.get("created"):
        console.print("[green]✓[/] 已生成 checkpoint 并写入时间线")
    else:
        console.print("[yellow]![/] 已有 checkpoint（使用 --force 覆盖）")
    console.print(Markdown(result.get("content") or ""))


@app.command()
def backup(
    dest: str = typer.Option("", "--dest", help="备份路径，默认 ~/.qr/backups/qr-时间戳.db"),
    restore: str = typer.Option("", "--restore", help="从指定备份恢复 qr.db"),
    verify: str = typer.Option("", "--verify", help="校验备份文件是否有效"),
    list_backups: bool = typer.Option(False, "--list", help="列出备份文件"),
):
    """备份 / 恢复 / 校验知识库数据库。"""
    from . import backup_ops

    db.init_db()
    if list_backups:
        rows = backup_ops.list_backup_files()
        if not rows:
            console.print("[dim]尚无备份[/]")
            return
        for p in rows:
            v = backup_ops.verify_backup(p)
            mark = "[green]✓[/]" if v.get("ok") else "[red]✗[/]"
            console.print(f"{mark} {p.name} · {v.get('size_mb', '?')} MB · {v.get('mtime', '')}")
        return
    if verify:
        rep = backup_ops.verify_backup(verify)
        if rep.get("ok"):
            console.print(f"[green]✓[/] 备份有效 · events={rep.get('events')} documents={rep.get('documents')}")
        else:
            console.print(f"[red]✗[/] {rep.get('error', '无效')}")
            raise typer.Exit(1)
        return
    if restore:
        rep = backup_ops.restore_backup(restore)
        if not rep.get("ok"):
            console.print(f"[red]✗[/] {rep.get('error', '恢复失败')}")
            raise typer.Exit(1)
        console.print(f"[green]✓[/] 已从 {rep['restored_from']} 恢复")
        if rep.get("safety_copy"):
            console.print(f"[dim]当前库已另存: {rep['safety_copy']}[/]")
        return
    result = backup_ops.run_backup(dest)
    console.print(f"[green]✓[/] 已备份到 {result['path']}")


@app.command(name="index-health")
def index_health_cmd(
    cleanup: bool = typer.Option(False, "--cleanup", help="清理源文件已消失的孤儿索引"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅统计不删除"),
):
    """索引健康检查与孤儿清理。"""
    from . import index_health

    db.init_db()
    with db.session() as conn:
        rep = index_health.scan(conn)
    console.print(f"文档 {rep['documents']} · 失效路径 {rep['missing_files']} · Cursor 转录 {rep['stale_cursor']}")
    for s in rep.get("missing_samples", [])[:5]:
        console.print(f"  [dim]· {s['path']}[/]")
    if cleanup or dry_run:
        with db.session() as conn:
            stats = index_health.cleanup_orphans(conn, dry_run=dry_run)
        action = "将清理" if dry_run else "已清理"
        console.print(f"[green]✓[/] {action} 文档 {stats['documents_removed']} · 块 {stats['chunks_removed']}")


@app.command(name="changelog")
def changelog_cmd(
    project: str = typer.Argument(..., help="项目 ID，如 dev/qr"),
    days: int = typer.Option(7, "--days"),
):
    """生成项目变更简报。"""
    from . import changelog

    pid = workspace.normalize_project_id(project)
    r = changelog.generate(pid, days=days)
    if r.get("error"):
        console.print(f"[red]{r['error']}[/]")
        raise typer.Exit(1)
    console.print(f"[green]✓[/] {r['path']}")
    console.print(r["content"][:2000])


@app.command()
def status():
    """查看知识库当前状态。"""
    db.init_db()
    with db.session() as conn:
        ev = conn.execute("SELECT source, COUNT(*) c FROM events GROUP BY source").fetchall()
        docs = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
        chunks = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
        summ = conn.execute("SELECT COUNT(*) c FROM summaries").fetchone()["c"]
    t = Table(title="QR本地知识库状态")
    t.add_column("项目"); t.add_column("数量", justify="right")
    for r in ev:
        t.add_row(f"事件 · {r['source']}", str(r["c"]))
    t.add_row("已索引文档", str(docs))
    t.add_row("向量块", str(chunks))
    t.add_row("历史总结", str(summ))
    console.print(t)
    console.print(f"数据目录: {config.QR_HOME}")
    rep = health.diagnose()
    if rep["issues"]:
        console.print(f"[yellow]![/] {len(rep['issues'])} 项待完善 → 运行 [bold]qr doctor[/]")


@app.command()
def ingest(source: str = typer.Option("all", help="all 或 shell/git/files/cursor/notes，逗号分隔")):
    """采集行为数据到知识库（增量）。"""
    db.init_db()
    sources = ALL_SOURCES if source == "all" else [s.strip() for s in source.split(",")]
    with db.session() as conn:
        with console.status("采集中..."):
            res = collectors.run(conn, sources)
    for k, v in res.items():
        if k in ("index_files", "index_chunks", "alerts"):
            continue
        console.print(f"[green]✓[/] {k}: 新增/更新 {v} 条事件")
    if res.get("index_files") is not None:
        console.print(
            f"[green]✓[/] 增量索引: {res.get('index_files', 0)} 文件, "
            f"{res.get('index_chunks', 0)} 向量块",
        )


def backfill_cmd(
    days: int = typer.Option(365, "--days", help="回溯天数，默认近一年"),
    source: str = typer.Option("all", help="all 或 shell/git/files/cursor/notes（逗号分隔）"),
):
    """全量补录：按真实时间倒追 shell / git / 文件 / Cursor / 笔记等开发行为。"""
    db.init_db()
    sources = backfill.BACKFILL_SOURCES if source == "all" else [s.strip() for s in source.split(",")]
    with db.session() as conn:
        with console.status(f"补录近 {days} 天行为中（shell / git / 文件 / Cursor / 笔记）..."):
            res = backfill.run(conn, days=days, sources=sources)
    console.print(f"[dim]时间范围: {res['since']} 至今[/]")
    for k in backfill.BACKFILL_SOURCES:
        if k in res:
            console.print(f"[green]✓[/] {k}: {res[k]} 条")
    if res.get("shell") == 0:
        console.print("[yellow]![/] shell 未补录到带时间戳的历史；请在 ~/.zshrc 启用 EXTENDED_HISTORY 后新命令才有准确时间")


@app.command()
def index(
    reindex: bool = typer.Option(False, "--reindex", help="忽略缓存全部重建"),
    since_days: float | None = typer.Option(
        None, "--since-days", help="仅索引近 N 天内修改过的文件",
    ),
    since_hours: float | None = typer.Option(
        None, "--since-hours", help="仅索引近 N 小时内修改过的文件",
    ),
    incremental: bool = typer.Option(
        False, "--incremental", help="仅索引上次 ingest/index 之后变更的文件",
    ),
):
    """对索引目录中的项目内容建立语义索引。"""
    db.init_db()
    roots = config.expand_paths(config.load_config()["index_roots"])
    console.print("索引目录: " + ", ".join(str(r) for r in roots))
    mode = "全量重建" if reindex else (
        "增量" if incremental or since_days or since_hours else "常规"
    )
    console.print(f"[dim]模式: {mode}[/]")
    try:
        with console.status("嵌入中（首次或大项目可能较慢）..."):
            stats = indexer.index(
                reindex=reindex,
                since_days=since_days,
                since_hours=since_hours,
                incremental=incremental,
            )
    except OllamaError as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    removed = stats.get("documents_removed", 0)
    extra = f"，清理禁入文档 {removed}" if removed else ""
    console.print(
        f"[green]✓[/] 新建/更新文档 {stats['files']}，向量块 {stats['chunks']}，"
        f"跳过 {stats['skipped']}（含 ~/.qr 配置）{extra}"
    )


@app.command("symbol")
def symbol_cmd(
    name: str = typer.Argument(..., help="函数 / 类 / 符号名"),
    project: str = typer.Option("", "--project", "-p", help="限定项目 dev/qr"),
    limit: int = typer.Option(20, "-n", help="最多条数"),
):
    """按符号名精确查找定义位置（需先 qr index）。"""
    from . import symbol_index

    db.init_db()
    hits = symbol_index.search(name, project=project or None, limit=limit)
    if not hits:
        console.print(f"[yellow]![/] 未找到符号 [bold]{name}[/]")
        raise typer.Exit(1)
    table = Table(show_header=True, header_style="bold")
    table.add_column("符号")
    table.add_column("类型")
    table.add_column("行")
    table.add_column("项目")
    table.add_column("路径")
    for h in hits:
        table.add_row(
            h["name"], h["kind"], str(h["line"]),
            h.get("project") or "", h["path"],
        )
    console.print(table)


def query_(text: str = typer.Argument(..., help="检索内容"),
           k: int = typer.Option(6, "-k", help="返回条数"),
           project: str = typer.Option("", "--project", "-p", help="限定项目（可 dev/qr）"),
           category: str = typer.Option("", "--category", "-c", help="限定分类 dev/mobile/...")):
    """语义检索项目内容（只返回片段，不调用大模型）。"""
    try:
        hits = query.search(
            text, k, project=project or None, category=category or None,
        )
    except OllamaError as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    if not hits:
        console.print("[yellow]没有命中。先把项目放入索引目录并运行 qr index。[/]"); return
    for i, h in enumerate(hits, 1):
        console.print(f"[cyan]{i}. {h['path']}[/]  相似度={h['score']:.3f}")
        console.print("   " + h["text"].strip().replace("\n", "\n   ")[:400] + "\n")


app.command(name="backfill")(backfill_cmd)
app.command(name="query")(query_)


@app.command()
def ask(text: str = typer.Argument(..., help="你的问题"),
        k: int = typer.Option(6, "-k", help="检索片段数"),
        project: str = typer.Option("", "--project", "-p", help="限定项目"),
        category: str = typer.Option("", "--category", "-c", help="限定分类"),
        model: str = typer.Option("", "--model", "-m", help="问答模型，见 qr ask --list-models"),
        deep: bool = typer.Option(False, "--deep", help="[兼容] 等同选默认推理模型"),
        web: bool = typer.Option(False, "--web", help="联网搜索（默认百度，被拦时回退必应）"),
        citations_only: bool = typer.Option(
            False, "--citations-only", help="只列检索出处，不调用生成模型",
        ),
        no_stream: bool = typer.Option(
            False, "--no-stream", help="等待完整回答后再显示（并渲染 Markdown）"),
        list_models: bool = typer.Option(False, "--list-models", help="列出可选问答模型")):
    """基于项目内容（可选联网）用本地大模型回答问题（默认流式输出）。"""
    from . import models as qr_models

    if list_models:
        try:
            with Ollama() as ol:
                installed = ol.health()
        except OllamaError:
            installed = []
        for m in qr_models.list_ask_models_with_status(installed):
            mark = "✓" if m["installed"] else "✗"
            d = " [默认]" if m.get("default") else ""
            console.print(f"[dim]{mark}[/] {m['id']:22} {m['label']}{d}")
            console.print(f"    {m.get('hint', '')}")
        raise typer.Exit(0)
    try:
        resolved = qr_models.resolve_ask_model(model or None, deep_legacy=deep)
    except ValueError as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)

    proj = project or None
    cat = category or None

    if citations_only:
        try:
            with console.status("检索本地出处…"):
                answer, hits = query.citations_only(
                    text, k, project=proj, category=cat,
                )
        except OllamaError as e:
            console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
        console.print(Markdown(answer))
        if hits:
            console.print("\n[dim]（未调用 chat 模型；嵌入检索仍可能使用 Ollama embed）[/]")
        return

    def _print_sources(hits, web_results):
        if hits:
            console.print("\n[dim]本地来源:[/]")
            for i, h in enumerate(hits, 1):
                console.print(f"  [dim]{i}. {h['path']} ({h['score']:.3f})[/]")
        if web_results:
            console.print("\n[dim]网络来源:[/]")
            for i, w in enumerate(web_results, 1):
                console.print(f"  [dim]{i}. {w['title']} — {w['url']} [{w['engine']}][/]")

    if no_stream:
        try:
            bits = ["检索"]
            if web:
                bits.append("联网搜索")
            bits.append(f"模型 {qr_models.model_label(resolved)}")
            with console.status(" + ".join(bits) + "中..."):
                answer, hits, web_results = query.ask(
                    text, k, model=resolved, web=web,
                    project=proj, category=cat,
                )
        except OllamaError as e:
            console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
        console.print(Markdown(answer))
        _print_sources(hits, web_results)
        return

    hits: list = []
    web_results: list = []
    answer = ""
    try:
        for ev in query.ask_stream(
            text, k, model=resolved, web=web, project=proj, category=cat,
        ):
            if ev["type"] == "status":
                console.print(f"[dim]{ev.get('text', '')}[/]")
            elif ev["type"] == "meta":
                hits = ev.get("hits") or []
                web_results = ev.get("web") or []
                similar = ev.get("similar") or []
                if similar:
                    console.print(
                        "[dim]相似历史: "
                        + " · ".join(s["title"] for s in similar[:3])
                        + "[/]"
                    )
            elif ev["type"] == "token":
                chunk = ev.get("text", "")
                answer += chunk
                sys.stdout.write(chunk)
                sys.stdout.flush()
            elif ev["type"] == "done":
                answer = ev.get("answer") or answer
    except OllamaError as e:
        console.print(f"\n[red]✗[/] {e}"); raise typer.Exit(1)
    if answer:
        sys.stdout.write("\n")
        sys.stdout.flush()
    _print_sources(hits, web_results)


@app.command()
def log(
    text: str = typer.Argument(..., help="笔记内容"),
    tags: str = typer.Option(None, "--tags", "-t"),
    project: str = typer.Option("", "-p", "--project", help="关联项目，如 experiments/idea"),
    kind: str = typer.Option(
        "note",
        "--type",
        help="note | decision | activity（非 Cursor 投入，如「今天在 idea 做了 2h 原型」）",
    ),
):
    """随手记录一条笔记/日志。

    活动记录示例：qr log \"今天在 experiments/idea 做了 2h 原型\" --type activity -p experiments/idea
    """
    db.init_db()
    if kind == "decision" and not text.strip().startswith("#"):
        text = (
            "# 决策记录\n\n"
            "## 问题\n\n\n"
            "## 选项\n\n- \n\n"
            "## 结论\n\n\n"
            "## 原因\n\n"
            + text
        )
    if kind == "activity" and not text.strip().startswith("[活动]"):
        text = f"[活动] {text.strip()}"
    proj = project.strip() or None
    with db.session() as conn:
        notes.add_note(conn, text, tags=tags, kind=kind, project=proj)
    console.print("[green]✓[/] 已记录")


def summarize(period: str = typer.Option("week", "--period", help="day/week/month"),
              date_from: str = typer.Option("", "--from", help="自定义起始 YYYY-MM-DD"),
              date_to: str = typer.Option("", "--to", help="自定义结束 YYYY-MM-DD"),
              show: bool = typer.Option(True, "--show/--no-show")):
    """生成周期性或自定义日期范围的行为总结。"""
    db.init_db()
    try:
        label = f"{date_from}~{date_to}" if date_from and date_to else period
        with console.status(f"生成 {label} 总结中（本地模型）..."):
            if date_from and date_to:
                out = summary.generate(date_from=date_from, date_to=date_to)
            elif date_from or date_to:
                console.print("[red]✗[/] 自定义总结需同时指定 --from 与 --to"); raise typer.Exit(1)
            else:
                out = summary.generate(period)
    except ValueError as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    except OllamaError as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    console.print(f"[green]✓[/] 已保存: {out}")
    if show:
        console.print(Markdown(out.read_text(encoding="utf-8")))


app.command(name="summary")(summarize)


@app.command()
def standards(edit: bool = typer.Option(False, "--edit", help="用 $EDITOR 打开编辑"),
              history: bool = typer.Option(False, "--history", help="查看历史版本列表"),
              restore: bool = typer.Option(False, "--restore", help="用仓库标准模板覆盖当前规范"),
              activate: int | None = typer.Option(
                  None, "--activate", min=1, help="将指定历史版本 ID 设为当前生效")):
    """查看或编辑个人规范（编辑后自动存为新版本）。"""
    path = governance.ensure_standards()
    if restore:
        governance.restore_standards_from_template()
        console.print("[green]✓[/] 已从标准模板恢复规范")
        return
    if activate is not None:
        try:
            governance.activate_version(activate)
        except ValueError as e:
            console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
        console.print(f"[green]✓[/] 已将版本 #{activate} 设为当前生效")
        return
    if history:
        t = Table(title="规范历史版本")
        t.add_column("ID"); t.add_column("时间"); t.add_column("说明")
        for v in governance.list_versions():
            t.add_row(str(v["id"]), v["date"], v["note"] or "")
        console.print(t); return
    if edit:
        before = path.read_text(encoding="utf-8")
        editor = os.environ.get("EDITOR", "nano")
        subprocess.call([editor, str(path)])
        after = path.read_text(encoding="utf-8")
        if after != before:
            governance.save_standards(after, note="手动编辑")
            console.print("[green]✓[/] 已保存为新版本")
    else:
        console.print(Markdown(path.read_text(encoding="utf-8")))
        console.print(
            f"\n[dim]文件: {path}（--edit 编辑 / --history 看历史 / "
            f"--activate ID 指定版本生效）[/]"
        )


@app.command(name="standards-revise")
def standards_revise(
    period: str = typer.Option("week", "--period", help="day/week/month"),
    from_conversations: bool = typer.Option(
        False,
        "--from-conversations",
        help="纳入全部 Cursor 对话摘录（含界面习惯写入第六章）",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认直接应用修订"),
):
    """根据近期行为/对话，用本地模型生成新版全局规范（保留历史）。"""
    from . import standards_revision

    governance.ensure_standards()
    cfg = config.load_config()
    try:
        label = "对话与行为" if from_conversations else "行为"
        with console.status(f"分析最近一{period}{label}并修订规范中..."):
            if yes or not standards_revision.needs_confirmation(cfg):
                if from_conversations:
                    new, recorded, changed, pending = governance.revise_from_conversations(
                        period, confirm=False
                    )
                else:
                    new, recorded, changed, pending = governance.revise_from_behavior(
                        period, confirm=False
                    )
            else:
                new, current, note = governance.propose_global_revision(
                    period, from_conversations=from_conversations
                )
                changed = governance.normalize_for_compare(current) != governance.normalize_for_compare(
                    new
                )
                if not changed:
                    recorded, pending = False, False
                else:
                    diff = standards_revision.diff_preview(current, new)
                    console.print("\n[bold]修订 diff 预览[/]（§一～§六 章节边界见草案）")
                    for sec in diff.get("sections") or []:
                        console.print(f"  [dim]§{sec['section']}[/] {sec['title']}")
                    console.print(standards_revision.format_cli_diff(diff))
                    if typer.confirm("应用此修订到生效规范？", default=False):
                        recorded = governance.save_standards(new, note=note)
                        standards_revision.clear_pending()
                        pending = False
                    else:
                        standards_revision.store_pending(
                            before=current,
                            after=new,
                            note=f"待确认：{note}",
                            period=period,
                            from_conversations=from_conversations,
                            source="cli",
                        )
                        recorded, pending = False, True
    except (OllamaError, ValueError) as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    if not changed:
        console.print("[yellow]![/] 模型输出与当前规范无实质差异，正文未变，未新建版本")
    elif pending:
        console.print(
            "[yellow]![/] 修订草案已保存为待确认（未覆盖当前规范）。"
            "执行 [bold]qr standards-confirm[/] 应用，或 Web 规范页确认。"
        )
    elif not recorded:
        console.print("[green]✓[/] 已更新生效规范（与上一归档版相同，未新建版本）")
    else:
        console.print("[green]✓[/] 已生成新版规范并存档")
    if pending:
        console.print(Markdown(new))
    else:
        console.print(Markdown(governance.read_standards()))


@app.command(name="standards-confirm")
def standards_confirm_cmd(
    note: str = typer.Option("", "--note", help="确认备注（写入沿革）"),
    reject: bool = typer.Option(False, "--reject", help="丢弃待确认修订"),
):
    """应用或丢弃待确认的规范修订草案。"""
    from . import standards_revision

    governance.ensure_standards()
    pending = standards_revision.load_pending()
    if not pending:
        console.print("[yellow]![/] 当前没有待确认的规范修订")
        raise typer.Exit(0)
    if reject:
        standards_revision.reject_pending()
        console.print("[green]✓[/] 已丢弃待确认修订")
        return
    diff = pending.get("diff") or {}
    console.print("[bold]待确认修订[/]")
    for sec in diff.get("sections") or []:
        console.print(f"  [dim]§{sec['section']}[/] {sec['title']}")
    console.print(standards_revision.format_cli_diff(diff))
    if not typer.confirm("确认应用到生效规范？", default=True):
        console.print("[dim]已取消[/]")
        raise typer.Exit(0)
    try:
        content, recorded = standards_revision.confirm_pending(note=note)
    except ValueError as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    if recorded:
        console.print("[green]✓[/] 已确认并新建归档版本")
    else:
        console.print("[green]✓[/] 已确认（与上一归档版相同，未新建版本）")
    console.print(Markdown(content))


@app.command(name="standards-pending")
def standards_pending_cmd():
    """查看待确认的规范修订草案与 diff。"""
    from . import standards_revision

    pending = standards_revision.load_pending()
    if not pending:
        console.print("[dim]无待确认修订[/]")
        raise typer.Exit(0)
    diff = pending.get("diff") or {}
    console.print(f"[bold]待确认[/] · {pending.get('note', '')}")
    for sec in diff.get("sections") or []:
        console.print(f"  [dim]§{sec['section']}[/] {sec['title']}")
    console.print(standards_revision.format_cli_diff(diff))
    console.print("\n[dim]应用：qr standards-confirm · 丢弃：qr standards-confirm --reject[/]")


@app.command(name="project-standards")
def project_standards_cmd(
    project: str = typer.Argument(..., help="项目 ID，如 dev/qr"),
    edit: bool = typer.Option(False, "--edit", help="用 $EDITOR 打开 PROJECT.md"),
    show: bool = typer.Option(False, "--show", help="打印当前项目规范"),
):
    """查看或编辑项目级规范（PROJECT.md）。"""
    from . import project_standards, workspace

    pid = workspace.normalize_project_id(project)
    proj_dir = workspace.resolve_project_dir(pid)
    if not proj_dir:
        console.print(f"[red]✗[/] 项目不存在: {project}"); raise typer.Exit(1)
    path = project_standards.ensure_project_standards(proj_dir, project_id=pid)
    if edit:
        before = path.read_text(encoding="utf-8")
        editor = os.environ.get("EDITOR", "nano")
        subprocess.call([editor, str(path)])
        after = path.read_text(encoding="utf-8")
        if after != before:
            project_standards.save_project_standards(
                proj_dir, after, project_id=pid, note="手动编辑"
            )
            governance.generate_rules(proj_dir)
            console.print("[green]✓[/] 已保存并更新 Cursor 规则")
    else:
        console.print(Markdown(path.read_text(encoding="utf-8")))
        console.print(f"\n[dim]文件: {path}[/]")


@app.command(name="project-standards-revise")
def project_standards_revise_cmd(
    project: str = typer.Argument(..., help="项目 ID"),
    period: str = typer.Option("week", "--period", help="day/week/month"),
):
    """根据本项目近期行为与 Cursor 对话修订 PROJECT.md。"""
    from . import project_standards

    try:
        with console.status(f"分析项目 {project} 最近一{period}…"):
            new, _ = project_standards.revise_from_conversations(project, period)
    except (OllamaError, ValueError) as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    console.print("[green]✓[/] 已更新项目规范与 10-project.mdc")
    console.print(Markdown(new))


@app.command()
def rules(
    target: str = typer.Option(".", "--target", help="目标项目目录"),
    all_projects: bool = typer.Option(
        False, "--all", help="为 ~/QR 下每个分类/项目生成 .cursor/rules 与 AGENTS.md",
    ),
    user: bool = typer.Option(
        False, "--user",
        help="导出 Cursor 全局 User Rules 片段（所有对话生效，需粘贴一次）",
    ),
):
    """根据个人规范生成 Cursor 规则；--user 覆盖全部对话，--all 覆盖 ~/QR 各项目。"""
    if user:
        path = governance.write_user_rules_snippet()
        console.print(f"[green]✓[/] 已写入 {path}")
        console.print(
            "[bold]在 Cursor 中一次性启用（全部对话）：[/]\n"
            "  1. 打开 Cursor Settings → Rules（或 Rules for AI / User Rules）\n"
            "  2. 将上述文件全文粘贴进「User Rules」并保存\n"
            "  3. 之后修改规范请 `qr standards --edit`，再执行 `qr rules --user` 更新粘贴内容\n"
        )
        return
    if all_projects:
        rows = governance.generate_rules_all_workspace()
        if not rows:
            console.print("[yellow]~/QR 下未发现项目目录。[/]"); return
        for proj, files in rows:
            console.print(f"[green]✓[/] {proj.relative_to(Path.home())}")
            for p in files:
                console.print(f"    {p.name}")
        console.print(f"[dim]共 {len(rows)} 个项目[/]")
        return
    written = governance.generate_rules(Path(target))
    for p in written:
        console.print(f"[green]✓[/] 已生成 {p}")


def _run_import_scattered(*, move: bool, yes: bool) -> None:
    console.print(
        "[dim]提示：长期维护推荐 [bold]qr workspace migrate --yes[/]；"
        "import 适合就地索引未迁入工作区的散落目录。[/]"
    )
    found = importer.discover()
    if not found:
        console.print("[yellow]未发现可导入的项目。[/]"); return
    console.print(f"发现 {len(found)} 个项目：")
    for p in found:
        console.print(f"  • {p}")
    if move:
        if not yes and not typer.confirm("确认将以上项目【移动】到 ~/QR 工作区？"):
            raise typer.Abort()
        for src, dst in importer.move_to_projects(found):
            console.print(f"[green]✓[/] 移动 {src} → {dst}")
        console.print("已移动，运行 [bold]qr index[/] 建立索引。")
    else:
        added = importer.add_to_index(found)
        console.print(f"[green]✓[/] 已加入索引目录 {len(added)} 个（就地索引，未移动文件）")
        console.print("运行 [bold]qr index[/] 建立索引。")


@app.command(name="import")
def import_projects(move: bool = typer.Option(False, "--move", help="把项目物理移动到 ~/QR/<分类>/（默认只就地索引）"),
                    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认")):
    """[已弃用] 请改用 qr workspace import。"""
    console.print("[yellow]![/] 已弃用：请使用 [bold]qr workspace import[/]")
    _run_import_scattered(move=move, yes=yes)


@app.command(name="web-watch")
def web_watch_cmd(
    interval: int = typer.Option(0, help="轮询间隔(秒)，0=读配置 web_watch_seconds"),
    once: bool = typer.Option(False, "--once", help="只探测一次（异常则重启后退出）"),
):
    """探测 Web 是否可访问；不可用时 kickstart 重启 launchd 服务。供 com.qr.web-watch 调用。"""
    cfg = config.load_config()
    if once:
        r = service_watch.watch_web_once()
        p = r["probe"]
        if r["healthy"]:
            console.print(f"[green]✓[/] Web 正常 · {p['url']}")
            return
        if r["restarted"]:
            console.print(
                f"[yellow]![/] 已尝试重启 Web · 当前: "
                f"{'正常' if r['healthy'] else p.get('detail') or '仍不可用'}"
            )
        else:
            console.print(f"[red]✗[/] Web 不可用且未安装后台服务: {p.get('detail')}")
        raise typer.Exit(1 if not r["healthy"] else 0)
    sec = interval or int(cfg.get("web_watch_seconds", 45))
    sec = max(30, sec)
    console.print(f"[green]✓[/] Web 健康巡检，每 {sec}s（Ctrl+C 退出）")
    while True:
        r = service_watch.watch_web_once()
        if r["restarted"]:
            ts = time.strftime("%H:%M:%S")
            state = "已恢复" if r["healthy"] else "仍异常"
            console.print(f"[dim]{ts}[/] kickstart Web → {state}")
        time.sleep(sec)


@app.command(name="cursor-retag")
def cursor_retag_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览，不写库"),
    sports: bool = typer.Option(
        True, "--sports/--all",
        help="默认仅归位体育相关问话到 dev/sports/project-sports",
    ),
    sessions: str = typer.Option(
        "",
        "--sessions",
        help="整段迁移会话 UUID（逗号分隔）；传 sports-pack 迁移三条体育主会话",
    ),
    project: str = typer.Option(
        "dev/sports/project-sports", "--project", help="--sessions 的目标项目 ID",
    ),
):
    """按问话内容或整段会话修正 Cursor 事件的 project 归属。"""
    from . import cursor_retag

    db.init_db()
    if sessions.strip():
        raw = sessions.strip()
        if raw in ("sports-pack", "sports", "pack"):
            ids = list(cursor_retag.KNOWN_SPORTS_SESSIONS.keys())
        else:
            ids = [s.strip() for s in raw.split(",") if s.strip()]
        pid = workspace.normalize_project_id(project)
        with db.session() as conn:
            if dry_run:
                prev = cursor_retag.preview_session_migrate(conn, ids, target=pid)
                t = Table(title=f"会话迁移预览 → {pid}")
                t.add_column("会话"); t.add_column("问话数", justify="right")
                t.add_column("将迁移", justify="right")
                for row in prev:
                    t.add_row(row["label"], row["turns"], row["move"])
                console.print(t)
                return
            stats = cursor_retag.migrate_cursor_sessions(
                conn, ids, target=pid, dry_run=False,
            )
        console.print(
            f"[green]✓[/] 已迁移 {stats['sessions']} 个会话："
            f" {stats['events']} 条问话 → {pid}，"
            f" 引导语 {stats['fragments']}，摘要 {stats['notes']}，归档 {stats['archives']}"
        )
        return

    if not sports:
        console.print("[yellow]请使用 --sports 或 --sessions[/]")
        raise typer.Exit(1)
    with db.session() as conn:
        if dry_run:
            prev = cursor_retag.preview_sports_retag(conn)
            console.print(f"[dim]→ dev/sports/project-sports：{len(prev['to_sports'])} 条[/]")
            for line in prev["to_sports"][:12]:
                console.print(f"  {line}")
            if len(prev["to_sports"]) > 12:
                console.print(f"  … 另有 {len(prev['to_sports']) - 12} 条")
            console.print(f"[dim]→ dev/qr（误标 sports）：{len(prev['to_qr'])} 条[/]")
            return
        stats = cursor_retag.apply_sports_retag(conn, dry_run=False)
    console.print(
        f"[green]✓[/] Cursor 归位："
        f" {stats['to_sports']} 条 → dev/sports/project-sports，"
        f" {stats['to_qr']} 条 → dev/qr，"
        f" 引导语 {stats['fragments']}，"
        f" 会话摘要 {stats['notes']}，"
        f" 归档 {stats['archives']}"
    )


@app.command(name="project-normalize")
def project_normalize_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览，不写库"),
    project: str = typer.Option("", "--project", help="仅迁移指定 legacy/canonical 项目"),
    legacy_only: bool = typer.Option(
        False, "--legacy-only", help="仅迁移 LEGACY_PROJECT_ALIASES",
    ),
):
    """归一化 project 标签：legacy 别名、索引子目录误标、Cursor 空标签补全。"""
    from . import project_normalize

    db.init_db()
    with db.session() as conn:
        audit = project_normalize.audit_project_labels(conn)
        if dry_run:
            prev = project_normalize.preview_legacy_projects(conn)
            t = Table(title="Legacy project 迁移预览")
            t.add_column("旧 ID")
            t.add_column("目标 ID")
            t.add_column("事件数", justify="right")
            for row in prev:
                t.add_row(row["legacy"], row["target"], str(row["events"]))
            if not prev:
                console.print("[dim]无待迁移的 legacy project[/]")
            else:
                console.print(t)
            doc_prev = project_normalize.preview_document_projects(conn)
            if doc_prev:
                t2 = Table(title="索引 project 对齐预览")
                t2.add_column("当前")
                t2.add_column("目标")
                t2.add_column("文档数", justify="right")
                for row in doc_prev[:20]:
                    t2.add_row(row["from"], row["to"], str(row["documents"]))
                console.print(t2)
            stats = project_normalize.run_full_normalize(
                conn, dry_run=True, only=project or None,
            )
            leg = stats["legacy"]
            docs = stats["documents"]
            cur = stats["cursor"]
            console.print(
                f"[dim]将更新：legacy events {leg['events']} · "
                f"documents {docs['documents']} · chunks {docs['chunks']} · "
                f"Cursor 补全 {cur['updated']}（跳过 {cur['skipped']}）[/]"
            )
            empty = audit.get("empty_cursor_events", 0)
            if empty:
                console.print(
                    f"[dim]当前 Cursor 空 project {empty} 条："
                    f"{audit.get('empty_cursor_by_slug')}[/]"
                )
            return
        if legacy_only:
            stats = project_normalize.migrate_legacy_projects(
                conn, dry_run=False, only=project or None,
            )
            console.print(
                f"[green]✓[/] legacy project 归一化："
                f" events {stats['events']}，FTS {stats['events_fts']}，"
                f" documents {stats['documents']}，chunks {stats['chunks']}"
            )
            return
        stats = project_normalize.run_full_normalize(
            conn, dry_run=False, only=project or None,
        )
    leg = stats["legacy"]
    docs = stats["documents"]
    cur = stats["cursor"]
    console.print(
        f"[green]✓[/] project 归一化："
        f" legacy events {leg['events']} · documents {docs['documents']} · "
        f"chunks {docs['chunks']} · symbols {docs['symbols']} · "
        f"Cursor 补全 {cur['updated']}"
    )


@app.command(name="cursor-watch")
def cursor_watch(interval: int = typer.Option(0, help="轮询间隔(秒)，0=读配置 cursor_poll_seconds")):
    """近实时同步 Cursor 对话到时间线。供 launchd 调用。"""
    cfg = config.load_config()
    sec = interval or int(cfg.get("cursor_poll_seconds", 60))
    sec = max(15, sec)
    db.init_db()
    console.print(f"[green]✓[/] Cursor 对话监听中，每 {sec}s 同步（Ctrl+C 退出）")
    while True:
        n = 0
        for attempt in range(4):
            try:
                with db.session() as conn:
                    n = cursor.collect(conn)
                break
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower() or attempt >= 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
        if n:
            console.print(f"[dim]{time.strftime('%H:%M:%S')}[/] 同步 {n} 条 Cursor 提问")
        time.sleep(sec)


@app.command()
def track(
    interval: int = typer.Option(tracker.SAMPLE_INTERVAL, help="采样间隔(秒)"),
    idle: int = typer.Option(tracker.IDLE_THRESHOLD, help="空闲阈值(秒)"),
    pause: str = typer.Option("", "--pause", help="暂停采集：2h / 30m / off（设置后退出，不常驻）"),
):
    """常驻运行应用使用追踪器（记录焦点应用时长/频率）。供 launchd 调用。"""
    if pause:
        try:
            result = tracker.set_pause(pause)
        except ValueError as e:
            console.print(f"[red]{e}[/]")
            raise typer.Exit(1)
        console.print(f"[green]✓[/] {result.get('message', '')}")
        return
    db.init_db()
    console.print(f"[green]✓[/] 应用追踪运行中（每 {interval}s 采样，空闲>{idle}s 不计时）。Ctrl+C 退出")
    tracker.run(interval=interval, idle_threshold=idle)


@app.command(name="track-once")
def track_once():
    """采样一次当前焦点应用与空闲时间（用于测试）。"""
    s = tracker.sample()
    db.init_db()
    with db.session() as conn:
        now = db.now()
        if s.get("app"):
            db.set_state(conn, "tracker_last_ok", str(now))
            db.set_state(conn, "tracker_last_error", "")
        elif s.get("warn"):
            db.set_state(conn, "tracker_last_error", s["warn"])
    console.print(s)


@app.command()
def power(
    action: str = typer.Argument(
        "status",
        help="status / on / off / toggle",
    ),
):
    """AI 服务开关：off 停 Ollama 并暂停屏幕采样；on 恢复。"""
    from . import power_mode

    act = action.strip().lower()
    if act in ("status", "st"):
        result = power_mode.status()
    elif act in ("on", "full", "enable"):
        result = power_mode.set_enabled(True)
    elif act in ("off", "lite", "disable"):
        result = power_mode.set_enabled(False)
    elif act in ("toggle", "t"):
        result = power_mode.toggle()
    else:
        console.print(f"[red]✗[/] 未知操作: {action}（可用 status / on / off / toggle）")
        raise typer.Exit(1)

    on = result.get("ai_enabled")
    console.print(
        f"[green]✓[/] {result.get('message', '')} · {result.get('hint', '')}"
        if on
        else f"[yellow]![/] {result.get('message', '')} · {result.get('hint', '')}"
    )
    if result.get("ollama_models_loaded"):
        console.print(f"[dim]Ollama 已加载模型: {result['ollama_models_loaded']}[/]")


def usage_cmd(period: str = typer.Option("day", "--period", help="day/week/month")):
    """查看应用使用统计（时长/占比/频率）。"""
    db.init_db()
    start, end = summary._window(period)
    rows, total = usage.stats(start, end)
    if not rows:
        console.print("[yellow]暂无应用使用数据。先运行 qr track（或装好追踪任务）。[/]"); return
    t = Table(title=f"应用使用 · 最近一{period} · 活跃 {usage._fmt(total)}")
    t.add_column("应用"); t.add_column("时长", justify="right")
    t.add_column("占比", justify="right"); t.add_column("切入次数", justify="right")
    for r in rows[:25]:
        t.add_row(r["app"], r["human"], f"{r['pct']}%", str(r["sessions"]))
    console.print(t)


app.command(name="usage")(usage_cmd)


@app.command(name="baidu-key")
def baidu_key(key: str = typer.Argument("", help="百度智能云千帆 API Key，留空则查看当前状态")):
    """设置/查看百度官方搜索 API Key（设置后联网搜索优先用官方接口）。"""
    cfg = config.load_config()
    if not key:
        cur = cfg.get("baidu_api_key", "").strip()
        console.print(f"当前 baidu_api_key: {'已设置 (' + cur[:6] + '…)' if cur else '未设置（用免费抓取/必应兜底）'}")
        return
    cfg["baidu_api_key"] = key.strip()
    config.save_config(cfg)
    import os
    os.chmod(config.CONFIG_PATH, 0o600)
    console.print("[green]✓[/] 已保存百度官方 API Key，联网搜索将优先使用官方接口。")
    console.print("[dim]测试：qr ask \"今天的新闻\" --web[/]")


@app.command()
def web(host: str = typer.Option("", help="监听地址，默认读 config.web_host"),
        port: int = typer.Option(0, help="端口，默认读 config.web_port"),
        install: bool = typer.Option(False, "--install", help="安装 launchd 后台服务"),
        uninstall: bool = typer.Option(False, "--uninstall", help="卸载 launchd 后台服务"),
        restart: bool = typer.Option(False, "--restart", help="重启已安装的 launchd Web 服务"),
        service_status: bool = typer.Option(False, "--status", help="查看后台服务状态")):
    """启动本地 Web 界面；加 --install 可后台常驻（launchd）。"""
    cfg = config.load_config()
    host = host or cfg.get("web_host", "127.0.0.1")
    port = port or int(cfg.get("web_port", 8765))
    if restart:
        if _restart_web_service():
            console.print(f"[green]✓[/] Web 服务已重启: http://{host}:{port}")
            for item in config.legacy_kb_findings():
                if item.get("area") == "schedule":
                    console.print(f"[yellow]![/] {item['message']}")
                    console.print(f"[dim]  → {item['fix']}[/]")
        else:
            console.print("[yellow]![/] 未安装后台服务，请先运行: qr web --install")
        return
    if install:
        _install_web_service(host=host, port=port)
        watch_sec = max(30, int(cfg.get("web_watch_seconds", 45)))
        console.print(f"[green]✓[/] Web 后台服务已安装: http://{host}:{port}")
        console.print(
            f"[green]✓[/] Web 健康巡检: 每 {watch_sec}s 探测，异常时自动 kickstart 重启"
        )
        console.print(f"[dim]日志: {config.LOGS_DIR / 'web.out.log'}、web-watch.out.log[/]")
        return
    if uninstall:
        _uninstall_web_service()
        console.print("[green]✓[/] Web 后台服务已卸载")
        return
    if service_status:
        _print_web_service_status(host=host, port=port)
        return
    from . import web as webmod
    console.print(f"[green]✓[/] Web 界面: http://{host}:{port}  (Ctrl+C 退出)")
    webmod.run(host=host, port=port)


workspace_app = typer.Typer(help="QR 工作区：~/QR 下按分类管理全部项目")
app.add_typer(workspace_app, name="workspace")


@workspace_app.command("status")
def workspace_status():
    """查看工作区路径与待迁移项目。"""
    cfg = config.load_config()
    root = workspace.ensure_workspace_layout(cfg)
    pending = workspace.discover_outside_workspace(cfg)
    console.print(f"[bold]工作区[/]: {root}")
    console.print(f"[dim]分类[/]: {', '.join(workspace.categories(cfg))}")
    console.print(f"[dim]索引根[/]: {', '.join(cfg.get('index_roots', []))}")
    if pending:
        console.print(f"\n[yellow]待迁入 {len(pending)} 个项目：[/]")
        for p in pending:
            console.print(f"  • {p}  → 建议 {workspace.infer_category(p, cfg)}/")
    else:
        console.print("\n[green]✓[/] 未发现工作区外的已登记项目")


@workspace_app.command("consolidate-qr")
def workspace_consolidate_qr(
    reinstall: bool = typer.Option(True, "--reinstall/--no-reinstall", help="pip install -e ~/QR/dev/qr"),
):
    """确认知识库在 ~/QR/dev/qr，移除 ~/Projects/qr 旧符号链接并重装 CLI。"""
    try:
        info = workspace.ensure_qr_repo_home()
    except ValueError as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    console.print(f"[green]✓[/] 知识库路径: {info['path']}")
    if info.get("legacy_link_removed"):
        console.print("[green]✓[/] 已移除 ~/Projects/qr 符号链接")
    else:
        console.print("[dim]~/Projects/qr 无需处理（非指向工作区的符号链接或不存在）[/]")
    if reinstall:
        dest = Path(info["path"])
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(dest)],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            console.print(f"[red]✗[/] pip install 失败:\n{r.stderr or r.stdout}")
            raise typer.Exit(1)
        console.print("[green]✓[/] 已 editable 安装:", dest)


@workspace_app.command("import")
def workspace_import(
    move: bool = typer.Option(False, "--move", help="把项目物理移动到 ~/QR/<分类>/（默认只就地索引）"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
):
    """发现散落项目并纳入知识库索引（就地或移动到工作区）。"""
    _run_import_scattered(move=move, yes=yes)


@workspace_app.command("migrate")
def workspace_migrate(
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览，不移动文件"),
    category: str = typer.Option("", "--category", "-c", help="强制指定分类"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
):
    """将 index_roots / 散落目录中的项目迁入 ~/QR/<分类>/<项目>。"""
    cfg = config.load_config()
    workspace.ensure_workspace_layout(cfg)
    paths = workspace.discover_outside_workspace(cfg)
    if not paths:
        workspace.apply_workspace_config(cfg)
        console.print("[green]✓[/] 无待迁移项目，已更新配置为 ~/QR 索引")
        return
    t = Table(title="迁移计划" if dry_run else "迁移项目")
    t.add_column("源"); t.add_column("目标"); t.add_column("分类")
    cat_force = category.strip() or None
    preview = workspace.migrate_paths(paths, category=cat_force, dry_run=True, cfg=cfg)
    for row in preview:
        if row.get("status") == "skipped_in_workspace":
            continue
        t.add_row(row["src"], row.get("dest", ""), row.get("category", ""))
    console.print(t)
    if dry_run:
        console.print("[dim]确认后执行: qr workspace migrate --yes[/]")
        return
    if not yes and not typer.confirm(f"确认迁移 {len(paths)} 个项目到 ~/QR ？"):
        raise typer.Abort()
    done = workspace.migrate_paths(paths, category=cat_force, dry_run=False, cfg=cfg)
    workspace.apply_workspace_config(cfg)
    console.print(f"[green]✓[/] 已迁移 {sum(1 for r in done if r.get('status')=='moved')} 个，配置已指向 ~/QR")
    console.print("[dim]建议: qr index --reindex[/]")


@workspace_app.command("sync-cursor-roots")
def workspace_sync_cursor_roots(
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览，不写注册表"),
):
    """扫描 ~/.cursor/projects，刷新 slug → project_id 注册表。"""
    payload = workspace.sync_cursor_roots_registry(persist=not dry_run)
    roots = payload.get("roots") or {}
    unmapped = payload.get("unmapped") or []
    console.print(f"[green]✓[/] 已注册 {len(roots)} 个 Cursor 工作区 slug")
    if unmapped:
        console.print(f"[yellow]![/] 未映射 {len(unmapped)} 个（采集将标 needs_review）")
        for slug in unmapped[:12]:
            console.print(f"  · {slug}")
        if len(unmapped) > 12:
            console.print(f"  … 另有 {len(unmapped) - 12} 个")
    if not dry_run:
        console.print(f"[dim]注册表: {workspace.CURSOR_ROOTS_PATH}[/]")


@workspace_app.command("remap-cursor")
def workspace_remap_cursor(
    dry_run: bool = typer.Option(False, "--dry-run", help="仅统计，不写库"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
):
    """按 cursor_roots 修正历史 Cursor 事件的 project 字段。"""
    db.init_db()
    if not dry_run and not yes:
        if not typer.confirm("将按注册表修正 Cursor 事件 project，继续？"):
            raise typer.Exit(0)
    with db.session() as conn:
        stats = workspace.remap_cursor_event_projects(conn, dry_run=dry_run)
    mode = "预览" if dry_run else "完成"
    console.print(
        f"[green]✓[/] {mode}：更新 {stats['updated']} · 清空错误 project {stats['cleared']} · "
        f"跳过 {stats['skipped']}"
    )


@workspace_app.command("audit")
def workspace_audit():
    """列出工作区真实项目与仅索引中的无效条目。"""
    audit = workspace.audit_projects()
    t = Table(title="工作区项目（~/QR 下真实目录）")
    t.add_column("项目 ID"); t.add_column("路径"); t.add_column("文档数")
    for row in audit["workspace"]:
        prot = " [保护]" if row.get("protected") else ""
        t.add_row(row["id"] + prot, row.get("path") or "—", str(row.get("docs", 0)))
    console.print(t)
    if audit["indexed_only"]:
        t2 = Table(title="仅索引中的无效条目（建议清理）")
        t2.add_column("项目 ID"); t2.add_column("文档数")
        for row in audit["indexed_only"]:
            t2.add_row(row["id"], str(row.get("docs", 0)))
        console.print(t2)
    junk = workspace.list_junk_project_ids()
    if junk:
        console.print(f"\n[dim]可一键清理: qr workspace prune --yes（共 {len(junk)} 项）[/]")


@workspace_app.command("prune")
def workspace_prune(
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认，清理无效项目"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅列出将删除项"),
):
    """删除无效项目：索引幽灵条目 + dev/qr-export 导出镜像。"""
    junk = workspace.list_junk_project_ids()
    if not junk:
        console.print("[green]✓[/] 没有需要清理的无用项目"); return
    console.print(f"将清理 {len(junk)} 项：")
    for pid in junk:
        console.print(f"  • {pid}")
    if dry_run:
        return
    if not yes and not typer.confirm("确认清理以上无用项目？此操作不可恢复"):
        raise typer.Abort()
    ok = 0
    for pid in junk:
        strict = _resolve_strict(pid)
        try:
            workspace.purge_project(
                pid,
                confirm=pid,
                confirm_phrase=workspace._DELETE_CONFIRM_PHRASE,
                strict_id=strict,
            )
            console.print(f"[green]✓[/] 已清理 {pid}")
            ok += 1
        except ValueError as e:
            console.print(f"[yellow]![/] 跳过 {pid}: {e}")
    console.print(f"[green]✓[/] 共清理 {ok}/{len(junk)} 项")


def _resolve_strict(pid: str) -> bool:
    """legacy 单段或无法对应 ~/QR 目录时用 strict_id。"""
    cat, name = workspace.parse_project_id(pid)
    if not cat or not name:
        return True
    return workspace._resolve_project_dir_exact(pid) is None


@workspace_app.command("delete")
def workspace_delete(
    project: str = typer.Argument(..., help="项目 ID，如 dev/my-app 或 my-app"),
):
    """永久删除项目：本地目录、索引、Cursor 对话文件等；保留时间线与引导语。"""
    try:
        pid = workspace.normalize_project_id(project)
        preview = workspace.preview_project_delete(pid)
    except ValueError as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)

    c = preview["counts"]
    console.print(f"[bold red]将删除项目[/] [cyan]{pid}[/]")
    console.print(f"路径: {preview['path']}")
    console.print(
        f"将删: 索引 {c['documents']} 文档 · {c['chunks']} 块 · Cursor 目录 {c.get('cursor_dirs', 0)} · "
        f"转录 {c.get('cursor_transcripts', 0)} · 问答 {c['chat_sessions']} · 事实 {c['facts']}"
    )
    console.print(
        f"[dim]保留: 时间线 {preview.get('retain', {}).get('timeline_events', c['events'])} 条 · 引导语[/]"
    )
    console.print(f"磁盘约: {preview['disk_bytes'] / (1024 * 1024):.1f} MB")

    if not typer.confirm("是否继续删除？", default=False):
        raise typer.Abort()
    phrase = typer.prompt(f"请输入「{workspace._DELETE_CONFIRM_PHRASE}」以确认")
    if phrase.strip() != workspace._DELETE_CONFIRM_PHRASE:
        console.print("[red]✗[/] 确认短语不正确，已取消"); raise typer.Exit(1)

    try:
        result = workspace.purge_project(
            pid, confirm=pid, confirm_phrase=phrase.strip(),
        )
    except ValueError as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    console.print(f"[green]✓[/] 已删除项目 {result['project']}")
    console.print(f"[dim]统计: {result['stats']}[/]")


@workspace_app.command("new")
def workspace_new(
    name: str = typer.Argument(..., help="项目名（小写中划线）"),
    category: str = typer.Option("", "--category", "-c", help="分类，默认 dev"),
):
    """在 ~/QR/<分类>/ 下创建新项目目录。"""
    cfg = config.load_config()
    try:
        dest = workspace.create_project(name, category=category or None, cfg=cfg)
    except ValueError as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    console.print(f"[green]✓[/] 已创建 {dest}")
    console.print("[dim]建议: cd 该目录 && git init && qr index[/]")


@app.command()
def desktop(
    install: bool = typer.Option(
        False, "--install", help="构建并安装「QR本地知识库」到桌面（替换旧 kb.app）",
    ),
    open_window: bool = typer.Option(
        False, "--open", help="打开原生窗口（pywebview，供 .app 启动器调用）",
    ),
    browser: bool = typer.Option(
        False, "--browser", help="用系统浏览器打开（旧行为）",
    ),
):
    """macOS 桌面应用：构建 .app 或打开原生窗口。"""
    from . import desktop_shell

    if open_window:
        desktop_shell.open_native_window()
        return
    if browser:
        desktop_shell.open_in_browser()
        return

    script = config.REPO_ROOT / "packaging" / "macos" / "build-app.sh"
    if not script.exists():
        console.print(f"[red]✗[/] 未找到构建脚本: {script}"); raise typer.Exit(1)
    args = [str(script)]
    if install:
        args.append("--desktop")
    r = subprocess.run(args, cwd=str(config.REPO_ROOT))
    if r.returncode != 0:
        raise typer.Exit(r.returncode)
    if install:
        console.print("[green]✓[/] 桌面应用已安装: ~/Desktop/QR本地知识库.app")
        console.print("[dim]双击打开原生窗口；旧浏览器方式: qr desktop --browser[/]")
    else:
        console.print(f"[dim]构建产物: {config.REPO_ROOT / 'packaging/macos/build/QR本地知识库.app'}[/]")
        console.print("[dim]安装到桌面: qr desktop --install[/]")


@app.command()
def optimize(
    skip_reindex: bool = typer.Option(False, "--skip-reindex"),
    skip_summary: bool = typer.Option(False, "--skip-summary"),
    skip_standards: bool = typer.Option(False, "--skip-standards"),
    skip_prompts: bool = typer.Option(False, "--skip-prompts"),
):
    """一键优化：收紧索引、清理噪声、同步规则、合并引导语、备份与周复盘。"""
    from . import optimize as opt

    db.init_db()
    console.print("[bold]优化前快照[/]")
    before = opt.metrics_snapshot()
    console.print(
        f"  索引根 {before['index_roots']} · 文档 {before['documents']} · "
        f"块 {before['chunks']} · 事件 {before['events_total']}"
    )
    with console.status("执行优化（含 reindex 可能较久）…"):
        result = opt.run(
            reindex=not skip_reindex,
            run_summary=not skip_summary,
            run_standards_auto=not skip_standards,
            merge_prompts=not skip_prompts,
        )
    after = result["after"]
    console.print("\n[bold green]✓[/] 优化完成")
    console.print(
        f"  文档 {before['documents']} → {after['documents']} · "
        f"块 {before['chunks']} → {after['chunks']} · "
        f"事件 {before['events_total']} → {after['events_total']}"
    )
    console.print(
        f"  引导语 {before['prompt_guides']} → {after['prompt_guides']} · "
        f"收件箱碎片 {before['prompt_fragments_inbox']} → {after['prompt_fragments_inbox']}"
    )
    console.print(f"  工作区项目: {', '.join(after['workspace_projects'])}")
    if result["steps"].get("backup"):
        console.print(f"[dim]备份: {result['steps']['backup']}[/]")
    console.print("[dim]请将 cursor-user-rules.md 粘贴到 Cursor User Rules（若尚未粘贴）[/]")


@app.command()
def update(
    summary_period: str = typer.Option("", "--summary", help="同时生成总结: day/week/month"),
    revise_standards: bool = typer.Option(
        False,
        "--revise-standards",
        help="据近期 Cursor 对话修订全局与活跃项目规范（忽略间隔）",
    ),
    index_health: bool = typer.Option(
        False,
        "--index-health",
        help="结束后清理源文件已消失的孤儿索引（亦受 index_health_auto 周期控制）",
    ),
):
    """一键更新：采集 + 索引（+可选总结 / 规范自动修订）。供定时任务调用。"""
    from . import standards_auto

    db.init_db()
    cfg = config.load_config()
    with db.session() as conn:
        res = collectors.run(conn, ALL_SOURCES)
    ev = {k: v for k, v in res.items() if k not in ("index_files", "index_chunks")}
    console.print("采集: " + ", ".join(f"{k}={v}" for k, v in ev.items()))
    try:
        if res.get("index_files") is not None:
            console.print(
                f"索引(增量): 文档 {res.get('index_files', 0)}, "
                f"块 {res.get('index_chunks', 0)}",
            )
        else:
            stats = indexer.index()
            console.print(f"索引: 文档 {stats['files']}, 块 {stats['chunks']}")
        if summary_period:
            out = summary.generate(summary_period)
            console.print(f"总结: {out}")
        run_std = revise_standards or (
            summary_period == "week" and cfg.get("standards_auto_on_weekly", True)
        )
        if run_std and (revise_standards or cfg.get("standards_auto_revise", True)):
            period = summary_period or "week"
            with console.status(f"据对话修订规范（{period}）…"):
                std_res = standards_auto.run_scheduled(period, force=revise_standards)
            if std_res.get("skipped"):
                console.print(f"[dim]规范修订: 跳过（{std_res.get('reason', '')}）[/]")
            else:
                g = std_res.get("global") or {}
                if g.get("ok"):
                    console.print(
                        f"[green]✓[/] 全局规范已修订"
                        + ("" if g.get("version_saved") else "（无实质变更）")
                    )
                elif g.get("error"):
                    console.print(f"[yellow]![/] 全局规范: {g['error']}")
                for p in std_res.get("projects") or []:
                    if p.get("ok"):
                        console.print(f"[green]✓[/] 项目 {p['project']} 规范已更新")
                    else:
                        console.print(f"[yellow]![/] 项目 {p['project']}: {p.get('error')}")
                if std_res.get("errors"):
                    console.print(f"[dim]日志: {config.LOGS_DIR / 'standards-auto.log'}[/]")
    except OllamaError as e:
        console.print(f"[yellow]![/] {e}")
    if cfg.get("evolution_auto_sync", True):
        from . import evolution_plan

        quick = summary_period != "week"
        try:
            evo = evolution_plan.sync(quick=quick, dry_run=False)
            if evo.get("changed"):
                if evo.get("promoted"):
                    console.print(
                        f"[green]✓[/] 进化计划已更新："
                        + "、".join(evo["promoted"])
                    )
                else:
                    console.print("[dim]进化计划已同步[/]")
        except Exception as e:
            console.print(f"[yellow]![/] 进化计划同步: {e}")
    from . import index_health as ih, ops_timeline

    with db.session() as conn:
        ih_rep = ih.maybe_auto_cleanup(conn, force=index_health)
    if ih_rep and ih_rep.get("ran"):
        cl = ih_rep.get("cleanup") or {}
        console.print(
            f"[green]✓[/] 索引健康：清理孤儿 {cl.get('documents_removed', 0)} 文档 · "
            f"{cl.get('chunks_removed', 0)} 块"
        )
        for p in ih_rep.get("sample_paths") or []:
            console.print(f"  [dim]· {p}[/]")
        ops_timeline.log_safe(
            action="index.health_auto",
            title="[知识库] 自动清理孤儿索引",
            content=(
                f"清理 {cl.get('documents_removed', 0)} 文档 · "
                f"{cl.get('chunks_removed', 0)} 块"
            ),
        )
    elif index_health and ih_rep and not ih_rep.get("ran"):
        console.print("[dim]索引健康：无孤儿文档需清理[/]")


@app.command(name="standards-auto")
def standards_auto_cmd(
    period: str = typer.Option("week", "--period", help="day/week/month"),
    force: bool = typer.Option(False, "--force", help="忽略间隔限制立即执行"),
    global_only: bool = typer.Option(False, "--global-only"),
    projects_only: bool = typer.Option(False, "--projects-only"),
):
    """手动触发：据 Cursor 对话修订全局 / 项目规范（与每周定时任务相同逻辑）。"""
    from . import standards_auto

    db.init_db()
    try:
        with console.status("修订规范中…"):
            res = standards_auto.run_scheduled(
                period,
                force=force,
                global_only=global_only,
                projects_only=projects_only,
            )
    except Exception as e:
        console.print(f"[red]✗[/] {e}")
        raise typer.Exit(1)
    if res.get("skipped"):
        console.print(f"[dim]已跳过: {res.get('reason')}[/]")
        console.print("[dim]使用 --force 可立即执行[/]")
        return
    g = res.get("global") or {}
    if g.get("pending"):
        console.print(
            "[yellow]![/] 全局规范修订已生成待确认草案（未覆盖当前规范）。"
            "执行 [bold]qr standards-confirm[/] 或 Web 规范页确认。"
        )
    console.print(json.dumps(res, ensure_ascii=False, indent=2))
    console.print(f"[dim]日志: {config.LOGS_DIR / 'standards-auto.log'}[/]")


def _install_web_service(host: str | None = None, port: int | None = None) -> None:
    from . import schedule_service

    schedule_service.install_web_agents(host=host, port=port)


def _uninstall_web_service() -> None:
    from . import schedule_service

    schedule_service.uninstall_web_agents()


def _restart_web_service() -> bool:
    from . import schedule_service

    return schedule_service.restart_web_service()


def _print_web_service_status(host: str, port: int) -> None:
    from . import schedule_service

    info = schedule_service.web_probe(host=host, port=port)
    probe = info["probe"]
    if probe["listening"] and probe["http_ok"]:
        console.print(f"[green]✓[/] Web 可访问 → http://{host}:{port}")
    elif info["loaded"]:
        console.print(
            f"[yellow]![/] launchd 已加载 Web，但探测失败: {probe.get('detail') or '无响应'}"
        )
        console.print("[dim]可执行: qr web-watch --once 尝试拉起[/]")
    elif info["plist_exists"]:
        console.print("[yellow]![/] 已安装但未加载，运行: qr web --install")
    else:
        console.print("[dim]未安装，运行: qr web --install[/]")
    if info["watch_plist_exists"]:
        state = "[green]运行中[/]" if info["watch_loaded"] else "[yellow]未加载[/]"
        console.print(f"[dim]健康巡检 com.qr.web-watch:[/] {state}")


@app.command()
def schedule(action: str = typer.Argument("install", help="install / uninstall / status"),
             every_hours: float = typer.Option(2, "--every-hours", help="自动收录间隔(小时)"),
             weekday: int = typer.Option(1, help="每周总结: 周几 0=周日..6=周六"),
             hour: int = typer.Option(9, help="每周/每日总结的运行小时"),
             daily: bool = typer.Option(False, "--daily", help="额外安装每日总结"),
             no_eval: bool = typer.Option(False, "--no-eval", help="不安装每月模型评测"),
             eval_day: int = typer.Option(0, "--eval-day", help="每月评测日（1-28，0=用 config）"),
             eval_hour: int = typer.Option(-1, "--eval-hour", help="每月评测小时（0-23，-1=用 config）")):
    """安装/卸载自动收录与分析（launchd）。

    install 默认安装两个任务：① 每 N 小时自动收录(采集+索引) ② 每周自动总结。
    """
    from . import schedule_service

    if action == "install":
        cfg = config.load_config()
        res = schedule_service.install_all(
            every_hours=every_hours,
            weekday=weekday,
            hour=hour,
            daily=daily,
            eval_monthly=not no_eval,
            eval_day=eval_day if eval_day > 0 else None,
            eval_hour=eval_hour if eval_hour >= 0 else None,
            include_web=True,
        )
        console.print("[green]✓[/] 应用追踪: 常驻记录焦点应用时长/频率（KeepAlive，开机自启）")
        console.print(
            f"[green]✓[/] Cursor 同步: 每 {res.get('cursor_poll_seconds', 60)}s 近实时收录对话提问"
        )
        console.print(
            f"[green]✓[/] 自动收录: 每 {every_hours} 小时运行 `qr update`（启动时也跑一次）"
        )
        console.print(
            f"[green]✓[/] 自动分析(周): 每周 weekday={weekday} {hour}:00 "
            f"`qr update --summary week`（含采集、周总结、据对话修订规范）"
        )
        web_host = cfg.get("web_host", "127.0.0.1")
        web_port = int(cfg.get("web_port", 8765))
        watch_sec = max(30, int(cfg.get("web_watch_seconds", 45)))
        console.print(f"[green]✓[/] Web 界面: 后台常驻 http://{web_host}:{web_port}（KeepAlive，开机自启）")
        console.print(f"[green]✓[/] Web 健康巡检: 每 {watch_sec}s 自动探测并重启")
        if daily:
            console.print(f"[green]✓[/] 自动分析(日): 每天 {hour}:30 生成日总结")
        if res.get("eval_monthly"):
            console.print(
                f"[green]✓[/] 模型评测(月): 每月 {res.get('eval_day', 1)} 日 "
                f"{res.get('eval_hour', 3):02d}:00 `qr eval run` → "
                f"{config.LOGS_DIR}/eval-YYYYMM.md"
            )
        console.print(f"[dim]日志: {config.LOGS_DIR}[/]")
        console.print(f"[dim]qr 可执行: {' '.join(config.resolve_qr_argv())}[/]")
        for item in config.legacy_kb_findings():
            if item.get("area") == "schedule":
                continue
            if item.get("level") == "info":
                console.print(f"[dim]· {item['message']}[/]")
            else:
                console.print(f"[yellow]![/] {item['message']}")
                console.print(f"[dim]  → {item['fix']}[/]")
    elif action == "uninstall":
        schedule_service.uninstall_all_agents()
        console.print("[green]✓[/] 已卸载全部 QR 定时任务")
    elif action == "status":
        t = Table(title="QR 定时任务")
        t.add_column("任务")
        t.add_column("状态")
        for label, state in schedule_service.agent_rows():
            if state == "运行中":
                disp = "[green]运行中[/]"
            elif state == "已安装未加载":
                disp = "已安装未加载"
            else:
                disp = "[dim]未安装[/]"
            t.add_row(label, disp)
        console.print(t)
    else:
        console.print("[red]未知操作，使用 install / uninstall / status[/]")


def digest_cmd(days: int = typer.Option(1, "--days", help="回溯天数")):
    """生成每日洞察摘要（行为 + 应用 + 项目）。"""
    r = digest.generate(days=days)
    console.print(f"[green]✓[/] 已保存: {r['path']}")
    console.print(r["content"][:1200])


app.command(name="digest")(digest_cmd)


def compliance_cmd(
    ship: bool = typer.Option(False, "--ship", help="设计者验收清单（决策 + ship-check/doctor）"),
    days: int = typer.Option(0, "--days", help="检查天数，默认 config compliance_ship_days"),
):
    """检查索引内各项目是否符合个人规范结构；--ship 检查近 N 天决策与验收。"""
    if ship:
        db.init_db()
        with db.session() as conn:
            rep = compliance.scan_ship_checks(
                conn, days=days if days > 0 else None,
            )
        span = rep["days"]
        console.print(f"[bold]设计者验收清单[/] · 近 {span} 天活跃项目")
        if rep.get("doctor_recent"):
            console.print("[dim]本机近期待办：已检测到 qr doctor 运行记录[/]")
        t = Table(title="项目验收")
        t.add_column("项目")
        t.add_column("活跃")
        t.add_column("决策")
        t.add_column("验收")
        t.add_column("说明")
        for p in rep.get("projects", []):
            if not p.get("active"):
                continue
            dec = str(p.get("decisions", 0))
            ship_mark = "✓" if p.get("ship_check") or rep.get("doctor_recent") else "✗"
            warn = "; ".join(p.get("warnings") or []) or "—"
            t.add_row(
                p["project"],
                "是",
                dec,
                ship_mark,
                warn,
            )
        console.print(t)
        if rep.get("missing_decisions"):
            console.print(
                f"[yellow]缺决策[/]: {', '.join(rep['missing_decisions'])}"
            )
        if rep.get("missing_ship"):
            console.print(
                f"[yellow]缺验收[/]: {', '.join(rep['missing_ship'])}"
            )
        if rep.get("ok"):
            console.print("[green]✓[/] 活跃项目决策与验收均达标")
        raise typer.Exit(0 if rep.get("ok") else 1)

    rows = compliance.scan_index_roots()
    t = Table(title="项目规范合规检查")
    t.add_column("项目")
    t.add_column("状态")
    t.add_column("问题")
    for r in rows[:30]:
        status = "[green]通过[/]" if r["ok"] else "[yellow]待改进[/]"
        t.add_row(r["path"].split("/")[-1], status, "; ".join(r["issues"][:2]) or "—")
    console.print(t)


app.command(name="compliance")(compliance_cmd)


def graph_cmd(limit: int = typer.Option(40, "-n")):
    """输出项目—来源—技术栈知识图谱摘要。"""
    g = compliance.knowledge_graph(limit=limit)
    console.print(f"节点 {len(g['nodes'])} · 边 {len(g['edges'])}")
    for e in g["edges"][:15]:
        console.print(f"  {e['from']} → {e['to']} ({e['weight']})")


app.command(name="graph")(graph_cmd)


@app.command(name="export-obsidian")
def export_obsidian_cmd(dest: str = typer.Option("", help="导出目录，默认 ~/Documents/QR-Export")):
    """导出笔记/总结/对话到 Obsidian 友好 Markdown。"""
    from pathlib import Path
    path = export.export_obsidian(Path(dest) if dest else None)
    console.print(f"[green]✓[/] 已导出到 {path}")


@app.command(name="export-bundle")
def export_bundle_cmd(
    dest: str = typer.Option("", "--dest", help="输出 zip 路径，默认 ~/.qr/bundles/"),
):
    """导出迁移包：qr.db + config + standards（不含 ~/QR 源码）。"""
    from . import bundle_export

    try:
        result = bundle_export.export_bundle(dest)
    except OSError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    console.print(f"[green]✓[/] 迁移包 → {result['path']}")
    console.print(f"[dim]含: {', '.join(result.get('files') or [])}[/]")


@app.command(name="import-bundle")
def import_bundle_cmd(
    path: str = typer.Argument(..., help="迁移包 zip 路径"),
    dest: str = typer.Option("", "--dest", help="目标目录，默认 ~/.qr"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅校验不解压"),
):
    """导入迁移包到新机器或目录。"""
    from . import bundle_export

    result = bundle_export.import_bundle(path, dest_home=dest, dry_run=dry_run)
    if not result.get("ok"):
        for err in result.get("errors") or [result.get("error", "失败")]:
            console.print(f"[red]✗[/] {err}")
        raise typer.Exit(1)
    if dry_run:
        console.print(f"[green]✓[/] 校验通过 · {', '.join(result.get('verified') or [])}")
        console.print(f"[dim]将导入到 {result.get('dest')}[/]")
    else:
        console.print(f"[green]✓[/] 已导入到 {result.get('dest')}")


cursor_app = typer.Typer(help="Cursor 归档与敏感内容")
app.add_typer(cursor_app, name="cursor")


@cursor_app.command("sanitize")
def cursor_sanitize_cmd(
    limit: int = typer.Option(25, "--limit", help="最多列出条数"),
):
    """扫描时间线 cursor 事件中的疑似密钥/令牌模式。"""
    from . import sensitive_scan

    db.init_db()
    with db.session() as conn:
        hits = sensitive_scan.scan_cursor_events(conn)
    if not hits:
        console.print("[green]✓[/] 未发现疑似敏感模式")
        return
    console.print(f"[yellow]![/] 发现 {len(hits)} 条含敏感模式（显示前 {limit} 条）")
    for h in hits[:limit]:
        console.print(f"  · {h['uid']}: {', '.join(h['patterns'])}")
        console.print(f"    [dim]{h['title']}[/]")
    console.print("[dim]请从 Cursor 归档源文件删除敏感内容后重新 ingest[/]")


@app.command()
def project(
    name: str = typer.Argument(..., help="项目 ID，如 dev/qr"),
    days: int = typer.Option(14, "--days"),
):
    """单项目体检面板（Git / Cursor / 合规 / 事实 / 样例检索）。"""
    pid = workspace.normalize_project_id(name)
    data = project_panel.panel(pid, days=days)
    if data.get("error"):
        console.print(f"[red]{data['error']}[/]")
        raise typer.Exit(1)
    console.print(f"[bold]{data['project']}[/] · 近 {data['window_days']} 天")
    if data.get("cursor_open_path"):
        console.print(f"\n[bold]Cursor 打开路径[/] {data['cursor_open_path']}")
        if data.get("cursor_slug"):
            console.print(f"[dim]  ~/.cursor/projects/{data['cursor_slug']}[/]")
    if data.get("git_commits"):
        console.print("\n[bold]Git[/]")
        for c in data["git_commits"][:5]:
            console.print(f"  {c['time']} {c['title']}")
    if data.get("cursor_topics"):
        console.print("\n[bold]Cursor[/]")
        for t in data["cursor_topics"][:5]:
            console.print(f"  · {t}")
    act = int(data.get("activity_notes") or 0)
    if act:
        console.print(f"\n[bold]活动记录[/] {act} 条（qr log --type activity）")
    elif data.get("notes_count"):
        console.print(f"\n[dim]笔记 {data['notes_count']} 条 · 活动记录 0[/]")
    comp = data.get("compliance")
    if comp:
        st = "[green]合规[/]" if comp.get("ok") else "[yellow]待改进[/]"
        console.print(f"\n{st} {comp.get('path', '')}")
    if data.get("stable_facts"):
        console.print("\n[bold]稳定事实[/]")
        for f in data["stable_facts"][:6]:
            console.print(f"  {f['key']}: {f['value']}")


@app.command(name="facts")
def facts_cmd(
    action: str = typer.Argument("list", help="list | sync | restore"),
    project: str = typer.Option(None, "--project"),
):
    """稳定事实记忆（长期配置与约定）。"""
    if action == "sync":
        rows = facts.sync_from_config()
        console.print(f"[green]✓[/] 已从 config 同步 {len(rows)} 条")
        return
    if action == "restore":
        rows = facts.restore_report_facts()
        console.print(f"[green]✓[/] 已恢复 {len(rows)} 条稳定事实 → {facts.FACTS_PATH}")
        return
    rows = facts.list_facts(project)
    if not rows:
        console.print("[dim]暂无事实，运行 qr facts sync[/]")
        return
    t = Table(title="稳定事实")
    t.add_column("键"); t.add_column("值"); t.add_column("项目")
    for r in rows:
        t.add_row(r["key"], r["value"], r.get("project") or "—")
    console.print(t)


@app.command(name="digest-notify")
def digest_notify_cmd(days: int = typer.Option(1, "--days")):
    """生成洞察并写入 latest + macOS 通知。"""
    r = alerts.publish_digest(days=days, notify=True)
    console.print(f"[green]✓[/] {r.get('latest')}")
    console.print(f"通知: {'已发送' if r.get('notified') else '未发送'}")


@app.command(name="mcp")
def mcp_cmd():
    """启动 MCP stdio 服务（供 Cursor 调用 QR本地知识库检索/问答）。"""
    from . import mcp_server
    mcp_server.main()


evolution_app = typer.Typer(help="进化计划验收与 docs/EVOLUTION_PLAN.md 同步")
app.add_typer(evolution_app, name="evolution")


@evolution_app.command("sync")
def evolution_sync_cmd(
    full: bool = typer.Option(False, "--full", help="含全量 RAG 基线验收（较慢）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只检测不写文件"),
):
    """按验收规则检测并更新 docs/EVOLUTION_PLAN.md。"""
    from . import evolution_plan

    db.init_db()
    with console.status("进化计划验收…"):
        res = evolution_plan.sync(quick=not full, dry_run=dry_run)
    for row in res.get("items") or []:
        mark = "✓" if row.get("passed") else "○"
        console.print(
            f"  {mark} #{row['num']} {row['title']}: {row['status_label']} — {row.get('detail', '')}"
        )
    if dry_run:
        console.print("[dim]dry-run：未写入文件[/]")
    elif res.get("promoted"):
        console.print(f"[green]✓[/] 新完成：{', '.join(res['promoted'])}")
    elif res.get("changed"):
        console.print(f"[green]✓[/] 已更新 {res.get('path')}")
    else:
        console.print("[dim]无变更[/]")


@evolution_app.command("status")
def evolution_status_cmd(
    full: bool = typer.Option(False, "--full", help="含全量 RAG 基线"),
):
    """查看进化计划验收状态（不写文件）。"""
    from . import evolution_plan

    db.init_db()
    rows = evolution_plan.evaluate(quick=not full)
    t = Table(title="进化计划验收")
    t.add_column("#")
    t.add_column("方向")
    t.add_column("状态")
    t.add_column("检测")
    for r in rows:
        t.add_row(
            str(r["num"]),
            r["title"],
            r["status_label"],
            r.get("detail", "")[:60],
        )
    console.print(t)


@app.command("ai-assess")
def ai_assess_cmd(
    save: bool = typer.Option(False, "--save", help="写入 ~/.qr/assessments/YYYY-MM-DD.md"),
    json_out: bool = typer.Option(False, "--json", help="输出 JSON 快照"),
):
    """每日 AI 使用水平快照（行为数据，便于纵向对比）。"""
    from . import ai_assess

    db.init_db()
    snap = ai_assess.collect_snapshot()
    if json_out:
        console.print(json.dumps(snap, ensure_ascii=False, indent=2))
    else:
        console.print(Markdown(ai_assess.format_markdown(snap)))
    if save:
        tpl = ai_assess.ensure_full_report_template()
        path = ai_assess.save_daily_report(snap=snap)
        console.print(f"[green]✓[/] 已保存 {path}")
        console.print(f"[dim]完整版评测模板: {tpl}[/]")


eval_app = typer.Typer(help="RAG / 模型评测（内置用例 + HTML 报告）")
app.add_typer(eval_app, name="eval")

prompts_app = typer.Typer(help="引导语：Cursor 问话采集、分类、合并")
app.add_typer(prompts_app, name="prompts")


@eval_app.command("run")
def eval_run_cmd():
    """全量模型评测（qwen + deepseek），写入 model_eval.json 与 ~/.qr/logs/eval-YYYYMM.md。"""
    from . import eval_runner

    config.ensure_dirs()
    console.print(
        "[dim]预计较久（双模型 × 用例数）；日志: "
        f"{config.LOGS_DIR / 'eval.out.log'}、eval.err.log[/]"
    )
    with console.status("运行模型评测…"):
        result = eval_runner.run_model_eval()
    if not result.get("ok"):
        console.print(f"[red]✗[/] {result.get('error', '评测失败')}")
        raise typer.Exit(1)
    console.print(f"[green]✓[/] 评测完成 → {result.get('path')}")
    if result.get("markdown"):
        console.print(f"[green]✓[/] 月度报告 → {result['markdown']}")
    if result.get("snapshot"):
        console.print(f"[dim]快照: {result['snapshot']}[/]")


@eval_app.command("compare-four")
def eval_compare_four():
    """四模型对比：先测 RAG 基线，再测各模型必达与生成速度，写入 ~/.qr/logs/model_compare_latest.html"""
    import subprocess
    import sys

    script = config.REPO_ROOT / "scripts" / "model_compare_four.py"
    if not script.exists():
        console.print(f"[red]✗[/] 未找到 {script}")
        raise typer.Exit(1)
    console.print("[dim]预计较久（4 模型 × 用例数）；报告: ~/.qr/logs/model_compare_latest.html[/]")
    r = subprocess.run([sys.executable, str(script)], check=False)
    if r.returncode != 0:
        raise typer.Exit(r.returncode)
    html = config.LOGS_DIR / "model_compare_latest.html"
    if html.exists():
        console.print(f"[green]✓[/] 报告: {html}")


@prompts_app.command("repair-times")
def prompts_repair_times():
    """从 Cursor 转录重算收件箱问话的真实时间戳。"""
    from . import prompt_guides

    db.init_db()
    with db.session() as conn:
        r = prompt_guides.repair_inbox_timestamps(conn)
    console.print(
        f"[green]✓[/] 更新 {r['updated']} 条 · 精确 {r['exact']} · 推算 {r['estimated']}"
    )


@prompts_app.command("sync")
def prompts_sync():
    """从 Cursor 事件同步问话碎片，并刷新 ~/.qr/prompts 笔记索引。"""
    from . import prompt_guides

    db.init_db()
    with db.session() as conn:
        r = prompt_guides.sync_cursor_inbox(conn)
        from .collectors import notes as notes_col

        notes_col.collect(conn)
        inbox = prompt_guides.stats(conn)["inbox"]
    rep = r.get("repair") or {}
    console.print(
        f"[green]✓[/] 新碎片 {r['new']} · 跳过 {r['skipped']} · 收件箱 {inbox} 条"
    )
    ex = int(r.get("excluded_by_session_title") or 0)
    if ex:
        console.print(f"[dim]非「执行-」对话未入收件箱：{ex} 条[/]")
    if rep:
        console.print(
            f"[dim]时间戳：精确 {rep.get('exact', 0)} · 推算 {rep.get('estimated', 0)}[/]"
        )


@prompts_app.command("purge-prefix")
def prompts_purge_prefix(
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览将删除的数量"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
):
    """按侧栏标题前缀清理引导语：仅保留「执行-」；参考/未确认不进引导语（时间线保留）。"""
    from . import prompt_guides

    db.init_db()
    with db.session() as conn:
        preview = prompt_guides.purge_non_execute_prompts(conn, dry_run=True)
    console.print(
        f"[dim]预览：删碎片 {preview['fragments_removed']}（收件箱 {preview['inbox_removed']}）· "
        f"删引导语 {preview['guides_removed']} · 保留 {len(preview['guides_kept'])} 条[/]"
    )
    if preview["guides_kept"]:
        console.print(f"[dim]保留引导语 id：{', '.join(str(i) for i in preview['guides_kept'])}[/]")
    if dry_run:
        return
    if not preview["fragments_removed"] and not preview["guides_removed"]:
        console.print("[green]✓[/] 无需清理")
        return
    if not yes and not typer.confirm("确认按「仅执行-」规则清理引导语？时间线不会删除。"):
        raise typer.Abort()
    with db.session() as conn:
        r = prompt_guides.purge_non_execute_prompts(conn, dry_run=False)
        st = prompt_guides.stats(conn)
    console.print(
        f"[green]✓[/] 已删碎片 {r['fragments_removed']} · 引导语 {r['guides_removed']} · "
        f"导出 md {r['exports_removed']}"
    )
    console.print(
        f"[dim]当前：收件箱 {st['inbox']} · 已保存引导语 {st['guides']}[/]"
    )


@prompts_app.command("list")
def prompts_list(
    inbox: bool = typer.Option(True, "--inbox/--guides", help="收件箱碎片 / 已保存引导语"),
    type_id: int | None = typer.Option(None, "--type"),
):
    """列出碎片或完整引导语。"""
    from . import prompt_guides

    db.init_db()
    with db.session() as conn:
        if inbox:
            rows = prompt_guides.list_fragments(conn, type_id=type_id)
            for r in rows:
                origin = r.get("fragment_origin", "auto")
                console.print(
                    f"[dim]#{r['id']}[/] [{r.get('type_name', '?')}] "
                    f"({'合并' if origin == 'merged' else '自动'}) "
                    f"{r['content'][:80]}…"
                )
        else:
            for g in prompt_guides.list_guides(conn, type_id=type_id):
                console.print(
                    f"[cyan]#{g['id']}[/] [{g.get('type_name', '?')}] "
                    f"({g.get('origin')}) {g['title']}"
                )


@prompts_app.command("delete-sessions")
def prompts_delete_sessions(
    session_ids: str = typer.Argument(..., help="Cursor 对话 session_id（uuid），逗号分隔"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
):
    """从知识库屏蔽整段 Cursor 对话（收件箱+时间线）；保留本机转录与归档文件。"""
    from . import prompt_guides

    sids = [x.strip() for x in session_ids.split(",") if x.strip()]
    if not sids:
        console.print("[red]✗[/] 未指定 session_id"); raise typer.Exit(1)
    if not yes and not typer.confirm(
        f"从知识库屏蔽 {len(sids)} 场 Cursor 对话（保留本机转录文件）？",
    ):
        raise typer.Abort()
    db.init_db()
    with db.session() as conn:
        r = prompt_guides.delete_cursor_sessions(conn, sids)
    console.print(
        f"[green]✓[/] 已屏蔽 对话 {r['sessions']} · 片段 {r['fragments']} · "
        f"时间线 {r['events']}",
    )


@prompts_app.command("delete")
def prompts_delete(
    ids: str = typer.Argument(..., help="碎片 id，逗号分隔"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
):
    """屏蔽收件箱中的问话片段（已合并的不可屏蔽）。"""
    from . import prompt_guides

    fid = [int(x.strip()) for x in ids.split(",") if x.strip()]
    if not fid:
        console.print("[red]✗[/] 未指定 id"); raise typer.Exit(1)
    if not yes and not typer.confirm(f"屏蔽 {len(fid)} 条收件箱片段？"):
        raise typer.Abort()
    db.init_db()
    with db.session() as conn:
        r = prompt_guides.delete_fragments(conn, fid)
    console.print(f"[green]✓[/] 已屏蔽 {r['deleted']} 条", end="")
    if r.get("skipped"):
        console.print(f"，跳过 {r['skipped']} 条（已合并）", end="")
    console.print()


@prompts_app.command("suggest-merge")
def prompts_suggest_merge(
    threshold: float = typer.Option(0.72, "--threshold", help="相似度阈值 0–1"),
    limit: int = typer.Option(200, "--limit", help="扫描收件箱上限"),
    json_out: bool = typer.Option(False, "--json", help="JSON 输出"),
):
    """收件箱片段相似聚类，给出可合并建议。"""
    from . import prompt_guides

    db.init_db()
    with db.session() as conn:
        clusters = prompt_guides.suggest_merge_clusters(
            conn, threshold=threshold, limit=limit,
        )
    if json_out:
        console.print(json.dumps(clusters, ensure_ascii=False, indent=2))
        return
    if not clusters:
        console.print("[dim]未发现可合并的相似片段组[/]")
        return
    t = Table(title="建议合并")
    t.add_column("片段 id")
    t.add_column("项目")
    t.add_column("条数")
    t.add_column("预览")
    for c in clusters:
        t.add_row(
            ",".join(str(x) for x in c["fragment_ids"]),
            c.get("project") or "—",
            str(c["count"]),
            c.get("preview") or "",
        )
    console.print(t)
    console.print("[dim]合并: qr prompts merge <id1,id2,...>[/]")


@prompts_app.command("merge")
def prompts_merge(
    ids: str = typer.Argument(..., help="碎片 id，逗号分隔，如 1,2,3"),
    title: str = typer.Option("", "--title"),
    type_name: str = typer.Option("", "--type", help="类型名，可新建"),
):
    """合并多段 Cursor 问话为一条完整引导语。"""
    from . import prompt_guides

    fid = [int(x.strip()) for x in ids.split(",") if x.strip()]
    db.init_db()
    with db.session() as conn:
        g = prompt_guides.merge_fragments(
            conn, fid, title=title or None, type_name=type_name or None,
        )
    title_safe = (g.get("title") or "").replace("[", "\\[")
    console.print(f"[green]✓[/] 引导语 #{g['id']}[/] {title_safe}")


@prompts_app.command("add")
def prompts_add(
    title: str = typer.Argument(...),
    body: str = typer.Argument(...),
    type_name: str = typer.Option("", "--type"),
):
    """手动创建完整引导语。"""
    from . import prompt_guides

    db.init_db()
    with db.session() as conn:
        g = prompt_guides.create_guide_manual(
            conn, title, body, type_name=type_name or None,
        )
    console.print(f"[green]✓[/] #{g['id']}[/] {title}")


@prompts_app.command("types")
def prompts_types():
    """列出引导语类型（内置 + 自定义）。"""
    from . import prompt_guides

    db.init_db()
    with db.session() as conn:
        t = Table(title="引导语类型")
        t.add_column("ID")
        t.add_column("名称")
        t.add_column("来源")
        t.add_column("收件箱")
        t.add_column("已保存")
        for row in prompt_guides.list_types(conn):
            origin = "内置" if row["type_origin"] == "auto" else "自定义"
            t.add_row(
                str(row["id"]),
                row["name"],
                origin,
                str(row.get("inbox_count", 0)),
                str(row.get("guide_count", 0)),
            )
        console.print(t)


refine_app = typer.Typer(help="引导语自动合并提炼（待确认）")
prompts_app.add_typer(refine_app, name="refine")


@refine_app.command("run")
def prompts_refine_run(
    limit: int = typer.Option(5, "--limit", help="本次最多生成几条提案"),
    inbox: bool = typer.Option(True, "--inbox/--no-inbox"),
    raw: bool = typer.Option(True, "--raw/--no-raw", help="包含待精炼的已合并引导语"),
):
    """扫描收件箱与 raw 引导语，用本地模型提炼为待确认提案。"""
    from . import prompt_refine
    from .ollama_client import OllamaError

    db.init_db()
    try:
        with db.session() as conn:
            r = prompt_refine.generate_proposals(
                conn, limit=limit, include_inbox=inbox, include_raw_guides=raw,
            )
    except OllamaError as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    console.print(f"[green]✓[/] 已生成 {r['created']} 条待确认提案")
    for p in r.get("proposals") or []:
        console.print(f"  #{p['id']} · {p.get('summary') or p.get('title')}")
    for err in r.get("errors") or []:
        console.print(f"[yellow]![/] {err}")


@refine_app.command("list")
def prompts_refine_list(
    status: str = typer.Option("pending", "--status", help="pending|approved|rejected"),
):
    """列出提炼提案。"""
    from . import prompt_refine

    db.init_db()
    with db.session() as conn:
        rows = prompt_refine.list_proposals(conn, status=status)
    if not rows:
        console.print("[dim]无提案[/]")
        return
    t = Table(title=f"引导语提炼 · {status}")
    t.add_column("ID")
    t.add_column("来源")
    t.add_column("标题")
    t.add_column("摘要")
    for p in rows:
        src = "收件箱" if p.get("source_kind") == "inbox_session" else f"引导语#{p.get('replace_guide_id')}"
        t.add_row(str(p["id"]), src, (p.get("title") or "")[:36], (p.get("summary") or "")[:48])
    console.print(t)


@refine_app.command("approve")
def prompts_refine_approve(
    proposal_id: int = typer.Argument(..., help="提案 id"),
    title: str = typer.Option("", "--title"),
    body: str = typer.Option("", "--body", help="覆盖正文（可空）"),
):
    """确认采纳提炼提案，写入正式引导语。"""
    from . import prompt_refine

    db.init_db()
    with db.session() as conn:
        try:
            r = prompt_refine.approve_proposal(
                conn,
                proposal_id,
                title=title or None,
                body=body or None,
            )
        except ValueError as e:
            console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    g = r.get("guide") or {}
    console.print(f"[green]✓[/] 已保存引导语 #{g.get('id')} · {g.get('title')}")


@refine_app.command("reject")
def prompts_refine_reject(proposal_id: int = typer.Argument(..., help="提案 id")):
    """拒绝提炼提案。"""
    from . import prompt_refine

    db.init_db()
    with db.session() as conn:
        try:
            prompt_refine.reject_proposal(conn, proposal_id)
        except ValueError as e:
            console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    console.print(f"[green]✓[/] 已拒绝 #{proposal_id}")


@eval_app.command("monthly")
def eval_monthly_cmd(
    save: bool = typer.Option(True, "--save/--no-save", help="写入 ~/.qr/eval_monthly/YYYY-MM.md"),
):
    """每月评测：检索基线 + AI 行为快照（使用 ~/.qr/templates/monthly_eval.md）。"""
    from . import monthly_eval

    with console.status("运行月度评测（检索基线 + AI 快照）…"):
        result = monthly_eval.run_monthly(save=save)
    rag = result.get("rag") or {}
    ext = (result.get("rag_split") or {}).get("extended") or {}
    console.print(
        f"[green]检索 core[/] {rag.get('retrieval_rate')}% "
        f"({rag.get('retrieval_ok')}/{rag.get('cases')}) · "
        f"泄漏 {rag.get('forbidden_hits')} · 均 {rag.get('search_avg')}s"
    )
    if ext.get("cases"):
        console.print(
            f"[dim]extended[/] {ext.get('retrieval_rate')}% "
            f"({ext.get('retrieval_ok')}/{ext.get('cases')}) · "
            f"泄漏 {ext.get('forbidden_hits')}"
        )
    ai = result.get("ai_assess") or {}
    console.print(
        f"[green]AI 快照[/] Cursor 归档 {ai.get('cursor_total')} · "
        f"近月 {ai.get('cursor_month_hours')}h · 决策 {ai.get('decision_notes')}"
    )
    if save and result.get("path"):
        console.print(f"[green]✓[/] 报告 → {result['path']}")
        console.print(f"[dim]模板: {result.get('template')}[/]")
    else:
        console.print(Markdown(result.get("markdown") or ""))


@eval_app.command("rag")
def eval_rag_only(
    extended: bool = typer.Option(
        False,
        "--extended",
        help="同时跑 extended 题集并分栏显示 core / extended 命中率",
    ),
):
    """仅跑检索基线（不调用四模型生成），用于快速检查索引质量。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "model_eval", config.REPO_ROOT / "scripts" / "model_eval.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    from . import eval_suite

    rows = mod.run_retrieval_baseline(include_extended=extended)
    if extended:
        split = eval_suite.summarize_rag_split(rows)
        core = split["core"]
        ext = split["extended"]
        console.print(
            f"[bold]core 门禁[/] {core['retrieval_rate']}% "
            f"({core['retrieval_ok']}/{core['cases']}) · "
            f"泄漏 {core['forbidden_hits']} · 均 {core['search_avg']}s"
        )
        console.print(
            f"[bold]extended[/] {ext['retrieval_rate']}% "
            f"({ext['retrieval_ok']}/{ext['cases']}) · "
            f"泄漏 {ext['forbidden_hits']} · 均 {ext['search_avg']}s"
        )
        console.print(f"[dim]题集说明: {eval_suite.EXTENDED_CASES_DOC}[/]")
    else:
        s = eval_suite.summarize_rag(rows)
        console.print(
            f"[green]检索命中率[/] {s['retrieval_rate']}% ({s['retrieval_ok']}/{s['cases']}) · "
            f"考题泄漏 {s['forbidden_hits']} 题 · 均检索 {s['search_avg']}s"
        )
        console.print("[dim]扩展题集：qr eval rag --extended[/]")
    for r in rows:
        mark = "✓" if r["retrieval_ok"] else "✗"
        leak = " [泄漏]" if r.get("retrieval_forbidden") else ""
        tier = r.get("tier", "core")
        console.print(f"  {mark} [{tier}] {r['case']}{leak}  {r['search_s']:.2f}s")


def main():
    argv = sys.argv[1:]
    skip_mirror = not argv or argv[0] in ("--help", "-h", "completion")
    job_id = ""
    orig_out = orig_err = None
    if not skip_mirror:
        from . import console_log, ops_timeline

        job_id = console_log.new_job_id("cli")
        label = ops_timeline.cli_label(argv)
        detail = " ".join(argv)[:500]
        console_log.job_start(source="cli", label=label, job_id=job_id, text=detail)
        orig_out, orig_err = sys.stdout, sys.stderr
        sys.stdout = _ConsoleTee(orig_out, "stdout", job_id)  # type: ignore[assignment]
        sys.stderr = _ConsoleTee(orig_err, "stderr", job_id)  # type: ignore[assignment]
        console.file = sys.stdout
    exit_code = 0
    try:
        app()
    except SystemExit as exc:
        exit_code = exc.code if exc.code is not None else 0
        if exit_code in (0, None) and argv:
            from . import ops_timeline

            ops_timeline.log_cli(argv)
        raise
    finally:
        if not skip_mirror and job_id:
            from . import console_log, ops_timeline

            if orig_out is not None:
                sys.stdout = orig_out
                console.file = orig_out
            if orig_err is not None:
                sys.stderr = orig_err
            label = ops_timeline.cli_label(argv)
            console_log.job_done(
                job_id,
                source="cli",
                label=label,
                error=bool(exit_code),
            )


if __name__ == "__main__":
    main()
