"""一键健康优化：索引收紧、规则同步、引导语合并、备份与复盘。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config, db, governance, indexer, project_standards, prompt_guides, shell_check, workspace
from .collectors import notes as notes_col

QR_PROJECT_MD = """# 项目约定 · qr

> QR 本地知识库程序仓库。全局规范见 `qr standards` / `00-personal-standards.mdc`。

## 用途
纯本地个人知识库：行为采集、时间线、向量检索/RAG、规范与 Cursor 规则、Web 控制台（8765）。

## 技术栈与结构
- Python 3.12+，包名 `qr`，入口 `qr/cli.py`
- 核心模块：`collectors/`、`indexer.py`、`query.py`、`web.py`、`governance.py`、`prompt_guides.py`
- 运行数据仅在 `~/.qr`；业务代码仅在 `~/QR/dev/qr`

## 开发约定
- 修改后：`pip install -e ~/QR/dev/qr` 与 `qr web --restart`
- 测试：`python3 -m unittest discover -s tests`
- 自检：`qr doctor`；勿恢复误删 `qr.db` 的沿革逻辑
- 索引默认仅 `~/QR`（见 `config.json` → `index_roots`）

## AI 协作（本项目）
- 先读 `README.md`、`docs/USE_CASES.md`
- 最小 diff；不提交除非用户要求
- 时间线 cursor 事件按 file 打开归档路径，不用弹窗
"""


def metrics_snapshot() -> dict[str, Any]:
    cfg = config.load_config()
    with db.session() as conn:
        events = dict(
            conn.execute(
                "SELECT source, COUNT(*) FROM events GROUP BY source"
            ).fetchall()
        )
        top_projects = conn.execute(
            "SELECT project, COUNT(*) c FROM documents WHERE project IS NOT NULL "
            "GROUP BY project ORDER BY c DESC LIMIT 12"
        ).fetchall()
        return {
            "index_roots": cfg.get("index_roots"),
            "events_total": sum(events.values()),
            "events": events,
            "documents": int(
                conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            ),
            "chunks": int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]),
            "summaries": int(
                conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]
            ),
            "chat_sessions": int(
                conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
            ),
            "prompt_fragments_inbox": int(
                conn.execute(
                    "SELECT COUNT(*) FROM prompt_guide_fragments WHERE guide_id IS NULL"
                ).fetchone()[0]
            ),
            "prompt_guides": int(
                conn.execute("SELECT COUNT(*) FROM prompt_guides").fetchone()[0]
            ),
            "top_index_projects": [(r[0], int(r[1])) for r in top_projects],
            "workspace_projects": workspace.list_projects_grouped()["projects"],
        }


def purge_index_outside_workspace() -> dict[str, int]:
    """删除索引中不在 ~/QR 与 ~/.qr 的文档（及向量块）。"""
    qr_root = workspace.workspace_root().resolve()
    qr_home = config.QR_HOME.resolve()
    removed = 0
    with db.session() as conn:
        rows = conn.execute("SELECT id, path FROM documents").fetchall()
        for r in rows:
            path_s = r["path"] or ""
            keep = False
            try:
                rp = Path(path_s).expanduser().resolve()
                try:
                    rp.relative_to(qr_root)
                    keep = True
                except ValueError:
                    try:
                        rp.relative_to(qr_home)
                        keep = True
                    except ValueError:
                        pass
            except OSError:
                pass
            if not keep:
                conn.execute("DELETE FROM documents WHERE id=?", (r["id"],))
                removed += 1
    return {"documents_removed": removed}


def prune_file_events_outside_workspace() -> int:
    allowed = set(workspace.list_projects_grouped(500)["projects"])
    with db.session() as conn:
        if not allowed:
            cur = conn.execute(
                "DELETE FROM events WHERE source='file' AND project IS NOT NULL"
            )
        else:
            ph = ",".join("?" * len(allowed))
            cur = conn.execute(
                f"DELETE FROM events WHERE source='file' AND project IS NOT NULL "
                f"AND project NOT IN ({ph})",
                list(allowed),
            )
        return int(cur.rowcount)


def tighten_index_config() -> list[str]:
    cfg = config.load_config()
    actions: list[str] = []
    if cfg.get("index_roots") != ["~/QR"]:
        cfg["index_roots"] = ["~/QR"]
        config.save_config(cfg)
        actions.append("index_roots → ['~/QR']")
    return actions


def sync_project_artifacts() -> list[str]:
    actions: list[str] = []
    proj_dir = workspace.resolve_project_dir("dev/qr")
    if proj_dir:
        path = proj_dir / project_standards.PROJECT_MD
        if path.read_text(encoding="utf-8") != QR_PROJECT_MD:
            path.write_text(QR_PROJECT_MD, encoding="utf-8")
            actions.append("PROJECT.md (dev/qr) 已更新")
        governance.generate_rules(proj_dir)
        actions.append("dev/qr Cursor 规则已刷新")
    governance.write_user_rules_snippet()
    actions.append(f"User Rules 片段 → {governance.USER_RULES_SNIPPET}")
    governance.generate_rules_all_workspace()
    actions.append("全部工作区项目 rules 已同步")
    return actions


def merge_inbox_by_session(*, max_merges: int = 8, min_fragments: int = 2) -> dict[str, int]:
    merged = 0
    with db.session() as conn:
        prompt_guides.ensure_schema(conn)
        groups = prompt_guides.list_inbox_groups(conn, limit=800)["groups"]
        for g in groups:
            if merged >= max_merges:
                break
            frags = g.get("fragments") or []
            if len(frags) < min_fragments:
                continue
            ids = [int(f["id"]) for f in frags]
            title = (g.get("title") or "合并引导语")[:120]
            if len(frags) > 1:
                title = f"{title[:60]}…（{len(frags)}段）"
            try:
                prompt_guides.merge_fragments(
                    conn,
                    ids,
                    title=title,
                    project="dev/qr" if g.get("project") in ("qr", "dev/qr", None, "") else g.get("project"),
                    tags=["auto-merge", "optimize"],
                )
                merged += 1
            except ValueError:
                continue
    return {"guides_merged": merged}


def seed_decision_logs() -> int:
    n = 0
    msgs = [
        ("决策：索引范围仅 ~/QR", "索引不再包含主目录 ~，RAG 与问答只面向工作区项目。"),
        ("决策：项目列表仅工作区", "下拉/检索/时间线屏蔽 Documents、Zomboid 等幽灵项目。"),
        ("决策：每周 update+summary+standards-auto", "由 com.qr.weekly 与 qr optimize 维护闭环。"),
    ]
    with db.session() as conn:
        for title, body in msgs:
            notes_col.add_note(conn, f"## {title}\n\n{body}", tags="decision,optimize", kind="decision")
            n += 1
    return n


def run(
    *,
    reindex: bool = True,
    run_summary: bool = True,
    run_standards_auto: bool = True,
    merge_prompts: bool = True,
) -> dict[str, Any]:
    db.init_db()
    before = metrics_snapshot()
    steps: dict[str, Any] = {}

    steps["config"] = tighten_index_config()
    steps["shell"] = shell_check.enable_extended_history()
    steps["index_purge"] = purge_index_outside_workspace()
    steps["events_prune"] = {"file_events_removed": prune_file_events_outside_workspace()}
    steps["project"] = sync_project_artifacts()

    if merge_prompts:
        steps["prompts"] = merge_inbox_by_session()

    steps["notes"] = {"decision_logs": seed_decision_logs()}

    import shutil
    from datetime import datetime

    config.ensure_dirs()
    backup_dir = config.QR_HOME / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"qr-pre-optimize-{stamp}.db"
    shutil.copy2(config.DB_PATH, backup_path)
    steps["backup"] = str(backup_path)

    if reindex:
        steps["index"] = indexer.index(reindex=True)

    with db.session() as conn:
        from . import collectors

        steps["ingest"] = {"shell": collectors.run(conn, ["shell"])}

    if run_summary:
        try:
            from . import summary

            out = summary.generate("week")
            steps["summary"] = str(out)
        except Exception as e:
            steps["summary"] = {"error": str(e)}

    if run_standards_auto:
        try:
            from . import standards_auto

            steps["standards_auto"] = standards_auto.run_scheduled(
                "week", force=True, global_only=True
            )
        except Exception as e:
            steps["standards_auto"] = {"error": str(e)}

    try:
        import subprocess

        subprocess.run(
            [config.resolve_qr_argv()[0], "schedule", "install"],
            check=False,
            capture_output=True,
        )
        steps["schedule"] = "install attempted"
    except Exception as e:
        steps["schedule"] = {"error": str(e)}

    after = metrics_snapshot()
    return {"before": before, "after": after, "steps": steps}
