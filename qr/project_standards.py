"""项目级规范：PROJECT.md + .cursor/rules/10-project.mdc。"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from . import config, db, governance, standards_digest, workspace

PROJECT_MD = "PROJECT.md"
PROJECT_RULE = "10-project.mdc"

_TEMPLATE = """# 项目约定 · {name}

> 本文件描述 **本项目** 的技术与协作约定；全局个人规范见 `qr standards` / `00-personal-standards.mdc`。
> 编辑：`qr project standards --edit {pid}` · 从对话修订：`qr project standards-revise {pid}`

## 用途
（一句话说明项目做什么）

## 技术栈与结构
- 语言 / 框架：
- 入口与关键目录：

## 开发约定
- 测试命令：
- 禁止修改的范围：
- 命名与风格（仅本项目）：

## AI 协作（本项目）
- 优先阅读的文件：
- 提交/PR 前检查：
"""

_PROJECT_REVISION_SYSTEM = (
    "你是项目级规范编辑助手。只输出该项目的 PROJECT.md 全文。"
    "只写与本仓库相关的技术栈、目录、测试、业务规则；"
    "不要重复全局目录/conda/Git 等个人习惯（那些由全局规范覆盖）。"
    "不要写 QR 知识库 Web 界面改版、单次 bug 修复过程。"
    "保持章节：用途、技术栈与结构、开发约定、AI 协作（本项目）。"
    "用简体中文 Markdown，从「# 项目约定」标题开始，不要解释。"
)

_INVALID_PROJECT_LINE = re.compile(
    r"^##\s*[五六七八九十]、|个人开发\s*/\s*存储|~/.qr/standards",
    re.MULTILINE | re.IGNORECASE,
)


def project_md_path(project_dir: Path) -> Path:
    return project_dir / PROJECT_MD


def read_project_standards(project_dir: Path) -> str | None:
    p = project_md_path(project_dir)
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8").strip() or None


def ensure_project_standards(project_dir: Path, *, project_id: str) -> Path:
    p = project_md_path(project_dir)
    if not p.exists():
        cat, name = workspace.parse_project_id(project_id)
        label = name or project_dir.name
        pid = project_id or workspace.project_from_path(project_dir)
        p.write_text(_TEMPLATE.format(name=label, pid=pid), encoding="utf-8")
    return p


def _sanitize_project_output(text: str) -> str:
    text = governance._sanitize_standards_output(text)
    marker = "# 项目约定"
    idx = text.find(marker)
    if idx > 0:
        text = text[idx:]
    lines = [ln for ln in text.splitlines() if not _INVALID_PROJECT_LINE.search(ln)]
    return "\n".join(lines).strip() + "\n"


def _is_valid_project_standards(text: str) -> bool:
    if len(text) < 80:
        return False
    need = ("## 用途", "## 技术栈", "## 开发约定")
    if not all(s in text for s in need):
        return False
    if _INVALID_PROJECT_LINE.search(text):
        return False
    return True


def save_project_standards(
    project_dir: Path,
    content: str,
    *,
    project_id: str,
    note: str = "手动更新",
) -> bool:
    cleaned = _sanitize_project_output(content)
    if not _is_valid_project_standards(cleaned):
        raise ValueError("项目规范格式无效，已拒绝保存")
    path = project_md_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    changed = True
    if path.is_file():
        changed = governance.normalize_for_compare(
            path.read_text(encoding="utf-8")
        ) != governance.normalize_for_compare(cleaned)
    if changed:
        path.write_text(cleaned, encoding="utf-8")
    db.init_db()
    with db.session() as conn:
        recorded = _record_project_version(conn, project_id, cleaned, note)
    return recorded


def _record_project_version(
    conn, project_id: str, content: str, note: str
) -> bool:
    row = conn.execute(
        "SELECT content FROM project_standards_versions "
        "WHERE project=? ORDER BY created_at DESC, id DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if row and governance.normalize_for_compare(row["content"] or "") == governance.normalize_for_compare(
        content
    ):
        return False
    conn.execute(
        "INSERT INTO project_standards_versions(project,content,note,created_at) "
        "VALUES(?,?,?,?)",
        (project_id, content, note, db.now()),
    )
    return True


def list_project_versions(project_id: str) -> list[dict[str, Any]]:
    db.init_db()
    with db.session() as conn:
        rows = conn.execute(
            "SELECT id, note, created_at FROM project_standards_versions "
            "WHERE project=? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "note": r["note"],
            "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"])),
        }
        for r in rows
    ]


def revise_from_conversations(
    project_id: str,
    period: str = "week",
) -> tuple[str, bool]:
    from . import summary
    from .ollama_client import Ollama

    pid = workspace.normalize_project_id(project_id)
    proj_dir = workspace.resolve_project_dir(pid)
    if not proj_dir or not proj_dir.is_dir():
        raise ValueError(f"项目不存在或不在工作区: {project_id}")
    ensure_project_standards(proj_dir, project_id=pid)
    current = read_project_standards(proj_dir) or ""
    start, end = summary._window(period)
    with db.session() as conn:
        ctx = standards_digest.build_revision_context(
            conn, start, end, project=pid, include_behavior=True
        )
    prompt = (
        f"# 当前项目规范\n\n{current}\n\n"
        f"# 最近一个 {period} 内与本项目相关的行为与对话\n\n{ctx}\n\n"
        "请输出修订后的**完整**项目 PROJECT.md。"
    )
    raw = Ollama().generate(
        prompt,
        system=_PROJECT_REVISION_SYSTEM,
        strip_think=True,
        timeout=float(config.load_config().get("standards_revise_timeout_seconds", 1800)),
    )
    new = _sanitize_project_output(raw)
    if not _is_valid_project_standards(new):
        raise ValueError("项目规范自动修订未通过校验，未写入文件")
    recorded = save_project_standards(
        proj_dir, new, project_id=pid, note=f"根据最近{period}对话与行为修订"
    )
    governance.generate_rules(proj_dir)
    return new, recorded


def project_rule_mdc(body: str) -> str:
    return (
        "---\n"
        "description: 本项目约定（由 QR 知识库根据 PROJECT.md 生成）\n"
        "globs:\n  - \"**/*\"\n"
        "alwaysApply: true\n"
        "---\n\n"
        "以下约定仅适用于本仓库；与全局个人规范冲突时，以**本项目**为准。\n\n"
        f"{body.strip()}\n"
    )
