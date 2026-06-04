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
def shell_check_cmd():
    """检查 zsh 历史是否适合 QR 行为补录。"""
    r = shell_check.check_extended_history()
    if r["ok"]:
        console.print(f"[green]✓[/] {r['message']}")
    else:
        console.print(f"[yellow]![/] {r['message']}")
        console.print(f"[dim]运行 qr shell enable 可自动写入配置[/]")


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
        models = Ollama().health()
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
def doctor():
    """检查各子系统边界状态并给出修复建议。"""
    db.init_db()
    rep = health.diagnose()
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


@app.command()
def backup(
    dest: str = typer.Option("", "--dest", help="备份路径，默认 ~/.qr/backups/qr-时间戳.db"),
):
    """备份知识库数据库。"""
    db.init_db()
    import shutil
    from datetime import datetime

    if dest:
        out = Path(dest).expanduser()
    else:
        d = config.QR_HOME / "backups"
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = d / f"qr-{stamp}.db"
    shutil.copy2(config.DB_PATH, out)
    console.print(f"[green]✓[/] 已备份到 {out}")


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
        console.print(f"[green]✓[/] {k}: 新增/更新 {v} 条事件")


@app.command()
def backfill_cmd(
    days: int = typer.Option(365, "--days", help="回溯天数，默认近一年"),
    source: str = typer.Option("all", help="all 或 shell/git/files/cursor"),
):
    """全量补录：按真实时间倒追 shell / git / 文件 / Cursor 等开发行为。"""
    db.init_db()
    sources = backfill.BACKFILL_SOURCES if source == "all" else [s.strip() for s in source.split(",")]
    with db.session() as conn:
        with console.status(f"补录近 {days} 天行为中（shell / git / 文件 / Cursor）..."):
            res = backfill.run(conn, days=days, sources=sources)
    console.print(f"[dim]时间范围: {res['since']} 至今[/]")
    for k in backfill.BACKFILL_SOURCES:
        if k in res:
            console.print(f"[green]✓[/] {k}: {res[k]} 条")
    if res.get("shell") == 0:
        console.print("[yellow]![/] shell 未补录到带时间戳的历史；请在 ~/.zshrc 启用 EXTENDED_HISTORY 后新命令才有准确时间")


@app.command()
def index(reindex: bool = typer.Option(False, "--reindex", help="忽略缓存全部重建")):
    """对索引目录中的项目内容建立语义索引。"""
    db.init_db()
    roots = config.expand_paths(config.load_config()["index_roots"])
    console.print("索引目录: " + ", ".join(str(r) for r in roots))
    try:
        with console.status("嵌入中（首次或大项目可能较慢）..."):
            stats = indexer.index(reindex=reindex)
    except OllamaError as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    removed = stats.get("documents_removed", 0)
    extra = f"，清理禁入文档 {removed}" if removed else ""
    console.print(
        f"[green]✓[/] 新建/更新文档 {stats['files']}，向量块 {stats['chunks']}，"
        f"跳过 {stats['skipped']}（含 ~/.qr 配置）{extra}"
    )


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
        list_models: bool = typer.Option(False, "--list-models", help="列出可选问答模型")):
    """基于项目内容（可选联网）用本地大模型回答问题。"""
    from . import models as qr_models

    if list_models:
        try:
            installed = Ollama().health()
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
    try:
        bits = ["检索"]
        if web:
            bits.append("联网搜索")
        bits.append(f"模型 {qr_models.model_label(resolved)}")
        with console.status(" + ".join(bits) + "中..."):
            answer, hits, web_results = query.ask(
                text, k, model=resolved, web=web,
                project=project or None, category=category or None,
            )
    except OllamaError as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    console.print(Markdown(answer))
    if hits:
        console.print("\n[dim]本地来源:[/]")
        for i, h in enumerate(hits, 1):
            console.print(f"  [dim]{i}. {h['path']} ({h['score']:.3f})[/]")
    if web_results:
        console.print("\n[dim]网络来源:[/]")
        for i, w in enumerate(web_results, 1):
            console.print(f"  [dim]{i}. {w['title']} — {w['url']} [{w['engine']}][/]")


@app.command()
def log(text: str = typer.Argument(..., help="笔记内容"),
        tags: str = typer.Option(None, "--tags", "-t"),
        kind: str = typer.Option("note", "--type", help="note 或 decision（决策日志）")):
    """随手记录一条笔记/日志。decision 类型用于结构化决策记录。"""
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
    with db.session() as conn:
        notes.add_note(conn, text, tags=tags, kind=kind)
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
):
    """根据近期行为/对话，用本地模型生成新版全局规范（保留历史）。"""
    governance.ensure_standards()
    try:
        label = "对话与行为" if from_conversations else "行为"
        with console.status(f"分析最近一{period}{label}并修订规范中..."):
            if from_conversations:
                new, _recorded = governance.revise_from_conversations(period)
            else:
                new, _recorded = governance.revise_from_behavior(period)
    except (OllamaError, ValueError) as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    console.print("[green]✓[/] 已生成新版规范并存档")
    console.print(Markdown(new))


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


@app.command(name="import")
def import_projects(move: bool = typer.Option(False, "--move", help="把项目物理移动到 ~/QR/<分类>/（默认只就地索引）"),
                    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认")):
    """发现散落在桌面/主目录等处的现有项目，纳入知识库索引。"""
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
def track(interval: int = typer.Option(tracker.SAMPLE_INTERVAL, help="采样间隔(秒)"),
          idle: int = typer.Option(tracker.IDLE_THRESHOLD, help="空闲阈值(秒)")):
    """常驻运行应用使用追踪器（记录焦点应用时长/频率）。供 launchd 调用。"""
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
def desktop(install: bool = typer.Option(
    False, "--install", help="构建并安装「QR本地知识库」到桌面（替换旧 kb.app）")):
    """macOS 桌面启动器（.app）。"""
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
):
    """一键更新：采集 + 索引（+可选总结 / 规范自动修订）。供定时任务调用。"""
    from . import standards_auto

    db.init_db()
    cfg = config.load_config()
    with db.session() as conn:
        res = collectors.run(conn, ALL_SOURCES)
    console.print("采集: " + ", ".join(f"{k}={v}" for k, v in res.items()))
    try:
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
    console.print(json.dumps(res, ensure_ascii=False, indent=2))
    console.print(f"[dim]日志: {config.LOGS_DIR / 'standards-auto.log'}[/]")


_AGENT_LABELS = [
    "com.qr.tracker", "com.qr.cursor", "com.qr.auto",
    "com.qr.weekly", "com.qr.daily", "com.qr.web", "com.qr.web-watch",
]
_LEGACY_AGENT_LABELS = [
    "com.qr.kb.tracker", "com.qr.kb.cursor", "com.qr.kb.auto",
    "com.qr.kb.weekly", "com.qr.kb.daily", "com.qr.kb.web",
]
_WEB_LABEL = "com.qr.web"
_WEB_WATCH_LABEL = "com.qr.web-watch"


def _uninstall_launch_agent(label: str) -> None:
    path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    subprocess.call(["launchctl", "unload", str(path)], stderr=subprocess.DEVNULL)
    if path.exists():
        path.unlink()


def _uninstall_legacy_agents() -> None:
    for label in _LEGACY_AGENT_LABELS:
        _uninstall_launch_agent(label)


def _plist(label: str, args: list[str], interval: int | None = None,
           calendar: dict | None = None, run_at_load: bool = False,
           keepalive: bool = False, throttle: int | None = None) -> str:
    qr_argv = config.resolve_qr_argv()
    arg_xml = "\n".join(f"    <string>{a}</string>" for a in [*qr_argv, *args])
    if keepalive:
        trigger = "  <key>KeepAlive</key><true/>"
    elif interval is not None:
        trigger = f"  <key>StartInterval</key><integer>{interval}</integer>"
    else:
        cal = "".join(f"    <key>{k}</key><integer>{v}</integer>\n"
                      for k, v in (calendar or {}).items())
        trigger = f"  <key>StartCalendarInterval</key>\n  <dict>\n{cal}  </dict>"
    throttle_xml = ""
    if throttle is not None:
        throttle_xml = f"  <key>ThrottleInterval</key><integer>{throttle}</integer>\n"
    name = label.rsplit(".", 1)[-1]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
{arg_xml}
  </array>
{trigger}
{throttle_xml}  <key>StandardOutPath</key><string>{config.LOGS_DIR / (name + '.out.log')}</string>
  <key>StandardErrorPath</key><string>{config.LOGS_DIR / (name + '.err.log')}</string>
  <key>RunAtLoad</key><{'true' if run_at_load else 'false'}/>
</dict>
</plist>
"""


def _install_agent(label: str, plist_text: str) -> None:
    path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plist_text)
    subprocess.call(["launchctl", "unload", str(path)], stderr=subprocess.DEVNULL)
    subprocess.call(["launchctl", "load", str(path)], stderr=subprocess.DEVNULL)


def _web_service_args(host: str, port: int) -> list[str]:
    return ["web", "--host", host, "--port", str(port)]


def _install_web_watch_service() -> None:
    cfg = config.load_config()
    sec = max(30, int(cfg.get("web_watch_seconds", 45)))
    _install_agent(
        _WEB_WATCH_LABEL,
        _plist(
            _WEB_WATCH_LABEL,
            ["web-watch"],
            interval=sec,
            run_at_load=True,
            throttle=20,
        ),
    )


def _install_web_service(host: str | None = None, port: int | None = None) -> None:
    cfg = config.load_config()
    host = host or cfg.get("web_host", "127.0.0.1")
    port = port or int(cfg.get("web_port", 8765))
    config.ensure_dirs()
    _install_agent(
        _WEB_LABEL,
        _plist(
            _WEB_LABEL,
            _web_service_args(host, port),
            keepalive=True,
            run_at_load=True,
            throttle=15,
        ),
    )
    _install_web_watch_service()


def _uninstall_web_service() -> None:
    for label in (_WEB_WATCH_LABEL, _WEB_LABEL):
        path = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
        subprocess.call(["launchctl", "unload", str(path)], stderr=subprocess.DEVNULL)
        if path.exists():
            path.unlink()


def _restart_web_service() -> bool:
    """重启已安装的 launchd Web 服务（加载最新代码）。"""
    path = Path.home() / "Library/LaunchAgents" / f"{_WEB_LABEL}.plist"
    if not path.exists():
        return False
    uid = os.getuid()
    label = f"gui/{uid}/{_WEB_LABEL}"
    r = subprocess.run(
        ["launchctl", "kickstart", "-k", label],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        subprocess.call(["launchctl", "unload", str(path)], stderr=subprocess.DEVNULL)
        subprocess.call(["launchctl", "load", str(path)], stderr=subprocess.DEVNULL)
    return True


def _print_web_service_status(host: str, port: int) -> None:
    path = Path.home() / "Library" / "LaunchAgents" / f"{_WEB_LABEL}.plist"
    watch_path = Path.home() / "Library/LaunchAgents" / f"{_WEB_WATCH_LABEL}.plist"
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    loaded = any(_WEB_LABEL in line for line in out.splitlines())
    watch_loaded = any(_WEB_WATCH_LABEL in line for line in out.splitlines())
    probe = service_watch.probe_web(host, port)
    if probe["listening"] and probe["http_ok"]:
        console.print(f"[green]✓[/] Web 可访问 → http://{host}:{port}")
    elif loaded:
        console.print(
            f"[yellow]![/] launchd 已加载 Web，但探测失败: {probe.get('detail') or '无响应'}"
        )
        console.print("[dim]可执行: qr web-watch --once 尝试拉起[/]")
    elif path.exists():
        console.print("[yellow]![/] 已安装但未加载，运行: qr web --install")
    else:
        console.print("[dim]未安装，运行: qr web --install[/]")
    if watch_path.exists():
        state = "[green]运行中[/]" if watch_loaded else "[yellow]未加载[/]"
        console.print(f"[dim]健康巡检 com.qr.web-watch:[/] {state}")


@app.command()
def schedule(action: str = typer.Argument("install", help="install / uninstall / status"),
             every_hours: float = typer.Option(2, "--every-hours", help="自动收录间隔(小时)"),
             weekday: int = typer.Option(1, help="每周总结: 周几 0=周日..6=周六"),
             hour: int = typer.Option(9, help="每周/每日总结的运行小时"),
             daily: bool = typer.Option(False, "--daily", help="额外安装每日总结")):
    """安装/卸载自动收录与分析（launchd）。

    install 默认安装两个任务：① 每 N 小时自动收录(采集+索引) ② 每周自动总结。
    """
    if action == "install":
        _uninstall_legacy_agents()
        config.ensure_dirs()
        interval = max(300, int(every_hours * 3600))
        _install_agent("com.qr.tracker",
                       _plist("com.qr.tracker", ["track"], keepalive=True, run_at_load=True))
        console.print("[green]✓[/] 应用追踪: 常驻记录焦点应用时长/频率（KeepAlive，开机自启）")
        cursor_sec = max(15, int(config.load_config().get("cursor_poll_seconds", 60)))
        _install_agent("com.qr.cursor",
                       _plist("com.qr.cursor", ["cursor-watch"], interval=cursor_sec, run_at_load=True))
        console.print(f"[green]✓[/] Cursor 同步: 每 {cursor_sec}s 近实时收录对话提问")
        _install_agent("com.qr.auto",
                       _plist("com.qr.auto", ["update"], interval=interval, run_at_load=True))
        console.print(f"[green]✓[/] 自动收录: 每 {every_hours} 小时运行 `qr update`（启动时也跑一次）")
        _install_agent("com.qr.weekly",
                       _plist("com.qr.weekly", ["update", "--summary", "week"],
                              calendar={"Weekday": weekday, "Hour": hour, "Minute": 0}))
        console.print(
            f"[green]✓[/] 自动分析(周): 每周 weekday={weekday} {hour}:00 "
            f"`qr update --summary week`（含采集、周总结、据对话修订规范）"
        )
        cfg = config.load_config()
        web_host = cfg.get("web_host", "127.0.0.1")
        web_port = int(cfg.get("web_port", 8765))
        _install_web_service(host=web_host, port=web_port)
        watch_sec = max(30, int(cfg.get("web_watch_seconds", 45)))
        console.print(f"[green]✓[/] Web 界面: 后台常驻 http://{web_host}:{web_port}（KeepAlive，开机自启）")
        console.print(f"[green]✓[/] Web 健康巡检: 每 {watch_sec}s 自动探测并重启")
        if daily:
            _install_agent("com.qr.daily",
                           _plist("com.qr.daily", ["summary", "--period", "day", "--no-show"],
                                  calendar={"Hour": hour, "Minute": 30}))
            console.print(f"[green]✓[/] 自动分析(日): 每天 {hour}:30 生成日总结")
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
        for label in _AGENT_LABELS:
            _uninstall_launch_agent(label)
        _uninstall_legacy_agents()
        console.print("[green]✓[/] 已卸载全部 QR 定时任务")
    elif action == "status":
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
        t = Table(title="QR 定时任务")
        t.add_column("任务"); t.add_column("状态")
        for label in _AGENT_LABELS:
            path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
            loaded = any(label in line for line in out.splitlines())
            state = "[green]运行中[/]" if loaded else ("已安装未加载" if path.exists() else "[dim]未安装[/]")
            t.add_row(label, state)
        console.print(t)
    else:
        console.print("[red]未知操作，使用 install / uninstall / status[/]")


@app.command()
def digest_cmd(days: int = typer.Option(1, "--days", help="回溯天数")):
    """生成每日洞察摘要（行为 + 应用 + 项目）。"""
    r = digest.generate(days=days)
    console.print(f"[green]✓[/] 已保存: {r['path']}")
    console.print(r["content"][:1200])


app.command(name="digest")(digest_cmd)


@app.command()
def compliance_cmd():
    """检查索引内各项目是否符合个人规范结构。"""
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


@app.command()
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


@app.command()
def project(
    name: str = typer.Argument(..., help="项目目录名，如 qr、ai-story-forge"),
    days: int = typer.Option(14, "--days"),
):
    """单项目知识面板（Git / Cursor / 合规 / 事实 / 样例检索）。"""
    data = project_panel.panel(name, days=days)
    if data.get("error"):
        console.print(f"[red]{data['error']}[/]")
        raise typer.Exit(1)
    console.print(f"[bold]{data['project']}[/] · 近 {data['window_days']} 天")
    if data.get("git_commits"):
        console.print("\n[bold]Git[/]")
        for c in data["git_commits"][:5]:
            console.print(f"  {c['time']} {c['title']}")
    if data.get("cursor_topics"):
        console.print("\n[bold]Cursor[/]")
        for t in data["cursor_topics"][:5]:
            console.print(f"  · {t}")
    comp = data.get("compliance")
    if comp:
        st = "[green]合规[/]" if comp.get("ok") else "[yellow]待改进[/]"
        console.print(f"\n{st} {comp.get('path', '')}")
    if data.get("stable_facts"):
        console.print("\n[bold]稳定事实[/]")
        for f in data["stable_facts"][:6]:
            console.print(f"  {f['key']}: {f['value']}")


@app.command()
def facts_cmd(
    action: str = typer.Argument("list", help="list | sync"),
    project: str = typer.Option(None, "--project"),
):
    """稳定事实记忆（长期配置与约定）。"""
    if action == "sync":
        rows = facts.sync_from_config()
        console.print(f"[green]✓[/] 已从 config 同步 {len(rows)} 条")
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


eval_app = typer.Typer(help="RAG / 模型评测（内置用例 + HTML 报告）")
app.add_typer(eval_app, name="eval")

prompts_app = typer.Typer(help="引导语：Cursor 问话采集、分类、合并")
app.add_typer(prompts_app, name="prompts")


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
    if rep:
        console.print(
            f"[dim]时间戳：精确 {rep.get('exact', 0)} · 推算 {rep.get('estimated', 0)}[/]"
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
    """删除整段 Cursor 对话（含本机转录 jsonl、收件箱与时间线）。"""
    from . import prompt_guides

    sids = [x.strip() for x in session_ids.split(",") if x.strip()]
    if not sids:
        console.print("[red]✗[/] 未指定 session_id"); raise typer.Exit(1)
    if not yes and not typer.confirm(
        f"永久删除 {len(sids)} 场 Cursor 对话（含转录文件）？",
    ):
        raise typer.Abort()
    db.init_db()
    with db.session() as conn:
        r = prompt_guides.delete_cursor_sessions(conn, sids)
    console.print(
        f"[green]✓[/] 对话 {r['sessions']} · 片段 {r['fragments']} · "
        f"事件 {r['events']} · 转录 {r['transcripts']} · 索引文档 {r['documents']}",
    )
    for err in r.get("errors") or []:
        console.print(f"[yellow]![/] {err}")


@prompts_app.command("delete")
def prompts_delete(
    ids: str = typer.Argument(..., help="碎片 id，逗号分隔"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
):
    """删除收件箱中的问话碎片（已合并的不可删）。"""
    from . import prompt_guides

    fid = [int(x.strip()) for x in ids.split(",") if x.strip()]
    if not fid:
        console.print("[red]✗[/] 未指定 id"); raise typer.Exit(1)
    if not yes and not typer.confirm(f"删除 {len(fid)} 条收件箱片段？"):
        raise typer.Abort()
    db.init_db()
    with db.session() as conn:
        r = prompt_guides.delete_fragments(conn, fid)
    console.print(f"[green]✓[/] 已删除 {r['deleted']} 条", end="")
    if r.get("skipped"):
        console.print(f"，跳过 {r['skipped']} 条（已合并）", end="")
    console.print()


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


@eval_app.command("rag")
def eval_rag_only():
    """仅跑检索基线（不调用四模型生成），用于快速检查索引质量。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "model_eval", config.REPO_ROOT / "scripts" / "model_eval.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    from . import eval_suite

    rows = mod.run_retrieval_baseline()
    s = eval_suite.summarize_rag(rows)
    console.print(
        f"[green]检索命中率[/] {s['retrieval_rate']}% ({s['retrieval_ok']}/{s['cases']}) · "
        f"考题泄漏 {s['forbidden_hits']} 题 · 均检索 {s['search_avg']}s"
    )
    for r in rows:
        mark = "✓" if r["retrieval_ok"] else "✗"
        leak = " [泄漏]" if r.get("retrieval_forbidden") else ""
        console.print(f"  {mark} {r['case']}{leak}  {r['search_s']:.2f}s")


def main():
    argv = sys.argv[1:]
    try:
        app()
    except SystemExit as exc:
        if exc.code in (0, None) and argv:
            from . import ops_timeline

            ops_timeline.log_cli(argv)
        raise


if __name__ == "__main__":
    main()
