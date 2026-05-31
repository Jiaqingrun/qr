from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from . import collectors, config, db, governance, indexer, query, summary
from .collectors import notes
from .ollama_client import Ollama, OllamaError

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="本地个人行为知识库与治理系统（离线，基于 ollama）")
console = Console()

ALL_SOURCES = ["shell", "git", "files", "cursor"]


@app.command()
def init():
    """初始化数据库、配置与个人规范。"""
    db.init_db()
    cfg = config.load_config()
    config.save_config(cfg)
    sp = governance.ensure_standards()
    console.print(f"[green]✓[/] 数据目录: {config.KB_HOME}")
    console.print(f"[green]✓[/] 数据库: {config.DB_PATH}")
    console.print(f"[green]✓[/] 配置: {config.CONFIG_PATH}")
    console.print(f"[green]✓[/] 个人规范: {sp}")
    try:
        models = Ollama().health()
        console.print(f"[green]✓[/] ollama 可用，模型: {', '.join(models)}")
    except OllamaError as e:
        console.print(f"[yellow]![/] {e}")


@app.command()
def status():
    """查看知识库当前状态。"""
    db.init_db()
    with db.session() as conn:
        ev = conn.execute("SELECT source, COUNT(*) c FROM events GROUP BY source").fetchall()
        docs = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
        chunks = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
        summ = conn.execute("SELECT COUNT(*) c FROM summaries").fetchone()["c"]
    t = Table(title="知识库状态")
    t.add_column("项目"); t.add_column("数量", justify="right")
    for r in ev:
        t.add_row(f"事件 · {r['source']}", str(r["c"]))
    t.add_row("已索引文档", str(docs))
    t.add_row("向量块", str(chunks))
    t.add_row("历史总结", str(summ))
    console.print(t)
    console.print(f"数据目录: {config.KB_HOME}")


@app.command()
def ingest(source: str = typer.Option("all", help="all 或 shell/git/files/cursor，逗号分隔")):
    """采集行为数据到知识库。"""
    db.init_db()
    sources = ALL_SOURCES if source == "all" else [s.strip() for s in source.split(",")]
    with db.session() as conn:
        with console.status("采集中..."):
            res = collectors.run(conn, sources)
    for k, v in res.items():
        console.print(f"[green]✓[/] {k}: 新增 {v} 条事件")


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
    console.print(f"[green]✓[/] 新建/更新文档 {stats['files']}，向量块 {stats['chunks']}，跳过 {stats['skipped']}")


def query_(text: str = typer.Argument(..., help="检索内容"),
           k: int = typer.Option(6, "-k", help="返回条数")):
    """语义检索项目内容（只返回片段，不调用大模型）。"""
    try:
        hits = query.search(text, k)
    except OllamaError as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    if not hits:
        console.print("[yellow]没有命中。先把项目放入索引目录并运行 kb index。[/]"); return
    for i, h in enumerate(hits, 1):
        console.print(f"[cyan]{i}. {h['path']}[/]  相似度={h['score']:.3f}")
        console.print("   " + h["text"].strip().replace("\n", "\n   ")[:400] + "\n")


app.command(name="query")(query_)


@app.command()
def ask(text: str = typer.Argument(..., help="你的问题"),
        k: int = typer.Option(6, "-k", help="检索片段数")):
    """基于项目内容用本地大模型回答问题。"""
    try:
        with console.status("检索 + 本地模型思考中..."):
            answer, hits = query.ask(text, k)
    except OllamaError as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    console.print(Markdown(answer))
    if hits:
        console.print("\n[dim]参考来源:[/]")
        for i, h in enumerate(hits, 1):
            console.print(f"  [dim]{i}. {h['path']} ({h['score']:.3f})[/]")


@app.command()
def log(text: str = typer.Argument(..., help="笔记内容"),
        project: str = typer.Option(None, "--project", "-p"),
        tags: str = typer.Option(None, "--tags", "-t")):
    """随手记录一条笔记/日志。"""
    db.init_db()
    with db.session() as conn:
        notes.add_note(conn, text, project=project, tags=tags)
    console.print("[green]✓[/] 已记录")


def summarize(period: str = typer.Option("week", "--period", help="day/week/month"),
              show: bool = typer.Option(True, "--show/--no-show")):
    """生成周期性行为总结。"""
    db.init_db()
    try:
        with console.status(f"生成 {period} 总结中（本地模型）..."):
            out = summary.generate(period)
    except OllamaError as e:
        console.print(f"[red]✗[/] {e}"); raise typer.Exit(1)
    console.print(f"[green]✓[/] 已保存: {out}")
    if show:
        console.print(Markdown(out.read_text(encoding="utf-8")))


app.command(name="summary")(summarize)


@app.command()
def standards(edit: bool = typer.Option(False, "--edit", help="用 $EDITOR 打开编辑")):
    """查看或编辑个人规范。"""
    path = governance.ensure_standards()
    if edit:
        editor = os.environ.get("EDITOR", "nano")
        subprocess.call([editor, str(path)])
    else:
        console.print(Markdown(path.read_text(encoding="utf-8")))
        console.print(f"\n[dim]文件: {path}（用 kb standards --edit 编辑）[/]")


@app.command()
def rules(target: str = typer.Option(".", "--target", help="目标项目目录")):
    """根据个人规范生成 .cursor/rules 与 AGENTS.md。"""
    written = governance.generate_rules(Path(target))
    for p in written:
        console.print(f"[green]✓[/] 已生成 {p}")


@app.command()
def update(summary_period: str = typer.Option("", "--summary", help="同时生成总结: day/week/month")):
    """一键更新：采集 + 索引（+可选总结）。供定时任务调用。"""
    db.init_db()
    with db.session() as conn:
        res = collectors.run(conn, ALL_SOURCES)
    console.print("采集: " + ", ".join(f"{k}={v}" for k, v in res.items()))
    try:
        stats = indexer.index()
        console.print(f"索引: 文档 {stats['files']}, 块 {stats['chunks']}")
        if summary_period:
            out = summary.generate(summary_period)
            console.print(f"总结: {out}")
    except OllamaError as e:
        console.print(f"[yellow]![/] {e}")


@app.command()
def schedule(action: str = typer.Argument("install", help="install / uninstall"),
             weekday: int = typer.Option(1, help="周几运行 0=周日..6=周六"),
             hour: int = typer.Option(9, help="几点运行")):
    """安装/卸载每周定时任务（launchd）。"""
    label = "com.qr.kb.weekly"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    kb_bin = Path(sys.executable).parent / "kb"
    if action == "install":
        config.ensure_dirs()
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{kb_bin}</string>
    <string>update</string>
    <string>--summary</string>
    <string>week</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>{weekday}</integer>
    <key>Hour</key><integer>{hour}</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>{config.LOGS_DIR / 'weekly.out.log'}</string>
  <key>StandardErrorPath</key><string>{config.LOGS_DIR / 'weekly.err.log'}</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
"""
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist)
        subprocess.call(["launchctl", "unload", str(plist_path)],
                        stderr=subprocess.DEVNULL)
        rc = subprocess.call(["launchctl", "load", str(plist_path)])
        console.print(f"[green]✓[/] 已安装: {plist_path}")
        console.print(f"  每周 weekday={weekday} {hour}:00 运行 `kb update --summary week`")
        if rc != 0:
            console.print("[yellow]![/] launchctl load 返回非零，可手动检查。")
    elif action == "uninstall":
        subprocess.call(["launchctl", "unload", str(plist_path)],
                        stderr=subprocess.DEVNULL)
        if plist_path.exists():
            plist_path.unlink()
        console.print("[green]✓[/] 已卸载定时任务")
    else:
        console.print("[red]未知操作，使用 install 或 uninstall[/]")


def main():
    app()


if __name__ == "__main__":
    main()
