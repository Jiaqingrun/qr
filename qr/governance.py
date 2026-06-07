from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from . import config, db

_TEMPLATE = config.REPO_ROOT / "standards" / "STANDARDS.md"

_PROMPT_LEAK_RE = re.compile(
    r"^#\s*我当前的个人规范\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_MARKDOWN_FENCE_RE = re.compile(r"^```(?:markdown)?\s*$|^```\s*$", re.MULTILINE)

# 行为摘要里常见的「本项目 UI 开发」噪声，不应升格为个人规范
_REVISION_NOISE = re.compile(
    r"界面|UI\s*改版|index\.html|问答界面|检索内容|颜色区分|视觉疲劳|"
    r"规范页|保存退出|流式|侧边栏|topbar|composer",
    re.IGNORECASE,
)

# 自动修订后若出现这些章节/句子，视为无效输出
_INVALID_STANDARD_LINE = re.compile(
    r"^##\s*[七八九十]、|问答界面|检索内容增加|规范页面根据日常",
    re.MULTILINE | re.IGNORECASE,
)

_REVISION_SYSTEM = (
    "你是用户的个人开发/存储/行为规范编辑助手。"
    "规范描述长期习惯：目录、conda、Git、AI 协作、复盘；"
    "**界面与视觉**仅写入「## 六、界面与视觉规范（全局）」一章（QR Web 与各项目共用的布局/组件习惯）。"
    "不要把单次任务过程、某次 bug 修复、某个业务项目的专属 UI 写进全局规范。"
    "保持六个主章节（一至六），不要新增第七、八章。"
    "命令行写 qr，数据目录写 ~/.qr，不要写 kb。"
    "只输出完整 Markdown 正文，从「# 个人开发 / 存储 / 行为规范」一行开始，不要解释、不要复述题目。"
)


def _seed_content() -> str:
    if _TEMPLATE.exists():
        return _TEMPLATE.read_text(encoding="utf-8")
    return "# 个人开发 / 存储 / 行为规范\n（尚未配置）\n"


def upgrade_standards_sections() -> bool:
    """将仓库模板中缺失的章节（如第六章 UI）合并进当前生效规范。"""
    current = read_standards()
    if "## 六、" in current:
        return False
    seed = _seed_content()
    idx = seed.find("## 六、")
    if idx < 0:
        return False
    block = seed[idx:].strip()
    merged = _sanitize_standards_output(current.rstrip() + "\n\n" + block + "\n")
    if not _is_valid_standards(merged):
        return False
    if normalize_for_compare(current) == normalize_for_compare(merged):
        return False
    config.STANDARDS_PATH.write_text(merged, encoding="utf-8")
    try:
        with db.session() as conn:
            record_version_if_changed(
                conn, merged, "合并模板新增章节（界面与视觉）"
            )
    except sqlite3.OperationalError:
        pass
    return True


def ensure_standards() -> Path:
    config.ensure_dirs()
    db.init_db()
    if not config.STANDARDS_PATH.exists():
        config.STANDARDS_PATH.write_text(_seed_content(), encoding="utf-8")
    else:
        try:
            upgrade_standards_sections()
        except (ValueError, sqlite3.OperationalError):
            pass
    with db.session() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM standards_versions").fetchone()["c"]
        if n == 0:
            conn.execute(
                "INSERT INTO standards_versions(content,note,created_at) VALUES(?,?,?)",
                (config.STANDARDS_PATH.read_text(encoding="utf-8"), "初始版本", db.now()))
    return config.STANDARDS_PATH


def read_standards() -> str:
    if config.STANDARDS_PATH.exists():
        return config.STANDARDS_PATH.read_text(encoding="utf-8")
    return _seed_content()


def normalize_for_compare(text: str) -> str:
    """用于判断两版规范是否实质相同（忽略行尾空白与末尾空行）。"""
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _latest_stored_content(conn) -> str | None:
    row = conn.execute(
        "SELECT content FROM standards_versions ORDER BY created_at DESC, id DESC LIMIT 1"
    ).fetchone()
    return row["content"] if row else None


def record_version_if_changed(conn, content: str, note: str) -> bool:
    """与最新归档版实质相同则不插入。返回是否新建版本。"""
    latest = _latest_stored_content(conn)
    if latest is not None and normalize_for_compare(latest) == normalize_for_compare(content):
        return False
    conn.execute(
        "INSERT INTO standards_versions(content,note,created_at) VALUES(?,?,?)",
        (content, note, db.now()),
    )
    return True


def prune_redundant_versions() -> dict[str, int]:
    """删除与上一版正文相同、或相对上一版无可展示 diff 的归档（保留首版）。"""
    from . import standards_changelog

    db.init_db()
    removed = 0
    with db.session() as conn:
        rows = conn.execute(
            "SELECT id, note, content FROM standards_versions ORDER BY created_at ASC, id ASC"
        ).fetchall()
        prev_row: dict | None = None
        for r in rows:
            rid = int(r["id"])
            note = (r["note"] or "").strip()
            norm = normalize_for_compare(r["content"] or "")
            drop = False
            if prev_row is not None:
                if norm == prev_row["norm"]:
                    drop = True
                elif not standards_changelog.skip_changelog_note(note):
                    diff = standards_changelog.diff_text(
                        prev_row["content"] or "",
                        r["content"] or "",
                    )
                    if not standards_changelog.has_substantive_change(diff):
                        drop = True
            if drop:
                conn.execute("DELETE FROM standards_versions WHERE id=?", (rid,))
                removed += 1
            else:
                prev_row = {"norm": norm, "content": r["content"] or ""}
    return {"removed": removed}


def prune_noise_versions() -> int:
    """删除测试/调试类备注的归档版本。"""
    from . import standards_changelog

    db.init_db()
    with db.session() as conn:
        rows = conn.execute(
            "SELECT id, note FROM standards_versions"
        ).fetchall()
        removed = 0
        for row in rows:
            if standards_changelog.skip_changelog_note(str(row["note"] or "")):
                conn.execute("DELETE FROM standards_versions WHERE id=?", (row["id"],))
                removed += 1
        return removed


def save_standards(content: str, note: str = "手动更新") -> bool:
    """保存生效规范；仅在有实质变更时新增归档版本。返回是否新建版本。"""
    config.ensure_dirs()
    db.init_db()
    cleaned = _sanitize_standards_output(content)
    if not _is_valid_standards(cleaned):
        raise ValueError("规范正文格式无效，已拒绝保存（请检查是否混入 UI 描述或 prompt 残留）")
    recorded = False
    with db.session() as conn:
        recorded = record_version_if_changed(conn, cleaned, note)
    current = read_standards() if config.STANDARDS_PATH.exists() else ""
    if normalize_for_compare(current) != normalize_for_compare(cleaned):
        config.STANDARDS_PATH.write_text(cleaned, encoding="utf-8")
    return recorded


def restore_standards_from_template(note: str = "恢复为标准模板") -> str:
    """用仓库内标准模板覆盖当前生效规范。"""
    content = _seed_content()
    config.ensure_dirs()
    db.init_db()
    with db.session() as conn:
        record_version_if_changed(conn, content, note)
    if normalize_for_compare(read_standards()) != normalize_for_compare(content):
        config.STANDARDS_PATH.write_text(content, encoding="utf-8")
    return content


def list_versions() -> list[dict]:
    db.init_db()
    with db.session() as conn:
        rows = conn.execute(
            "SELECT id, note, created_at FROM standards_versions ORDER BY created_at DESC"
        ).fetchall()
    return [{"id": r["id"], "note": r["note"],
             "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"]))}
            for r in rows]


def get_version(vid: int) -> str | None:
    db.init_db()
    with db.session() as conn:
        r = conn.execute("SELECT content FROM standards_versions WHERE id=?", (vid,)).fetchone()
    return r["content"] if r else None


def activate_version(vid: int, note: str | None = None) -> str:
    """将指定历史版本设为当前生效规范（写入 standards.md 并归档一条记录）。"""
    raw = get_version(vid)
    if raw is None:
        raise ValueError(f"版本 #{vid} 不存在")
    content = _sanitize_standards_output(raw)
    current = read_standards() if config.STANDARDS_PATH.exists() else ""
    if normalize_for_compare(content) == normalize_for_compare(current):
        return content
    label = note or f"设为生效（源自版本 #{vid}）"
    config.ensure_dirs()
    db.init_db()
    with db.session() as conn:
        record_version_if_changed(conn, content, label)
    config.STANDARDS_PATH.write_text(content, encoding="utf-8")
    return content


def _sanitize_standards_output(text: str) -> str:
    text = _THINK_RE.sub("", text or "")
    text = _MARKDOWN_FENCE_RE.sub("", text)
    text = _PROMPT_LEAK_RE.sub("", text)
    text = text.replace("~/.kb", "~/.qr").replace("`kb ", "`qr ")
    text = text.replace("`kb standards", "`qr standards").replace("`kb log", "`qr log")
    text = text.replace("`kb rules", "`qr rules").replace("`kb summary", "`qr summary")
    # 若模型重复粘贴了「当前规范」块，保留从正式标题开始的一段
    marker = "# 个人开发 / 存储 / 行为规范"
    idx = text.find(marker)
    if idx > 0:
        text = text[idx:]
    lines = [ln for ln in text.splitlines() if not _INVALID_STANDARD_LINE.search(ln)]
    return "\n".join(lines).strip() + "\n"


def _is_valid_standards(text: str) -> bool:
    if len(text) < 200:
        return False
    required = (
        "## 一、",
        "## 二、",
        "## 三、",
        "## 四、",
        "## 五、",
        "## 六、",
    )
    if not all(s in text for s in required):
        return False
    if _INVALID_STANDARD_LINE.search(text):
        return False
    if "我当前的个人规范" in text:
        return False
    return True


def _digest_for_revision(conn, start: int, end: int) -> str:
    """供规范修订用的行为摘要：弱化 QR 项目 UI 开发噪声。"""
    from . import summary

    raw = summary._digest(conn, start, end)
    if not raw:
        return ""
    lines: list[str] = []
    for line in raw.splitlines():
        if _REVISION_NOISE.search(line):
            continue
        if line.strip().startswith("- ") and "qr" in line.lower() and "界面" in line:
            continue
        lines.append(line)
    out = "\n".join(lines).strip()
    if not out:
        return "（近期行为摘要无适合纳入规范的长期习惯信号，请仅做措辞微调。）"
    return out


def revise_from_behavior(period: str = "week") -> tuple[str, bool]:
    """基于近期行为摘要修订全局规范（不含对话摘录）。"""
    return _revise_global(period, from_conversations=False)


def revise_from_conversations(period: str = "week") -> tuple[str, bool]:
    """基于行为摘要 + 全部 Cursor 对话摘录修订全局规范。"""
    return _revise_global(period, from_conversations=True)


def _revise_global(period: str, *, from_conversations: bool) -> tuple[str, bool]:
    from . import standards_digest, summary
    from .ollama_client import Ollama

    start, end = summary._window(period)
    with db.session() as conn:
        if from_conversations:
            digest = standards_digest.build_revision_context(
                conn, start, end, project=None, include_behavior=True
            )
            src = f"最近一个{period}的行为与全部 Cursor 对话"
        else:
            digest = _digest_for_revision(conn, start, end)
            src = f"最近一个{period}的行为摘要"
    current = read_standards()
    prompt = (
        f"# 当前生效的个人规范（请在此基础上微调，勿推翻结构）\n\n{current}\n\n"
        f"# {src}\n\n{digest}\n\n"
        "请输出修订后的**完整**规范 Markdown。"
    )
    raw = Ollama().generate(prompt, system=_REVISION_SYSTEM, strip_think=True)
    new = _sanitize_standards_output(raw)
    if not _is_valid_standards(new):
        raise ValueError(
            "自动修订结果未通过校验（可能章节结构错误或混入了单次任务过程），"
            "未写入文件。请手动编辑，或先执行 qr standards --restore 恢复模板。"
        )
    note = (
        f"根据最近{period}对话与行为自动修订"
        if from_conversations
        else f"根据最近{period}行为自动修订"
    )
    recorded = save_standards(new, note=note)
    return new, recorded


def _personal_rule_mdc(standards: str) -> str:
    return (
        "---\n"
        "description: 个人开发/存储/行为规范（由 QR本地知识库 生成，勿手改本文件）\n"
        "globs:\n"
        "alwaysApply: true\n"
        "---\n\n"
        "以下是用户的**全局**个人规范；本项目另有 `10-project.mdc` 约定。\n\n"
        f"{standards}\n"
    )


def _agents_md(standards: str, project_body: str | None) -> str:
    parts = [
        "# AGENTS.md\n\n",
        "> 由 QR 知识库生成：`00-personal-standards.mdc`（全局）+ `10-project.mdc`（本项目）。\n\n",
        "## 全局个人规范\n\n",
        f"{standards.strip()}\n",
    ]
    if project_body:
        parts.extend(["\n## 本项目约定\n\n", f"{project_body.strip()}\n"])
    return "".join(parts)


def generate_rules(target: Path) -> list[Path]:
    from . import project_standards

    standards = read_standards()
    target = target.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    rules_dir = target / ".cursor" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    rule_path = rules_dir / "00-personal-standards.mdc"
    rule_path.write_text(_personal_rule_mdc(standards), encoding="utf-8")
    written.append(rule_path)

    proj_body = project_standards.read_project_standards(target)
    if proj_body:
        proj_rule = rules_dir / project_standards.PROJECT_RULE
        proj_rule.write_text(project_standards.project_rule_mdc(proj_body), encoding="utf-8")
        written.append(proj_rule)

    agents_path = target / "AGENTS.md"
    agents_path.write_text(_agents_md(standards, proj_body), encoding="utf-8")
    written.append(agents_path)
    return written


def iter_workspace_projects(cfg: dict | None = None):
    from . import workspace

    cfg = cfg or config.load_config()
    root = workspace.workspace_root(cfg)
    for cat in workspace.categories(cfg):
        cat_dir = root / cat
        if not cat_dir.is_dir():
            continue
        for proj in sorted(cat_dir.iterdir()):
            if proj.is_dir() and not proj.name.startswith("."):
                yield proj


def generate_rules_all_workspace(cfg: dict | None = None) -> list[tuple[Path, list[Path]]]:
    out: list[tuple[Path, list[Path]]] = []
    for proj in iter_workspace_projects(cfg):
        out.append((proj, generate_rules(proj)))
    return out


USER_RULES_SNIPPET = config.QR_HOME / "cursor-user-rules.md"


def write_user_rules_snippet() -> Path:
    """生成供 Cursor「User Rules」一次性粘贴的全局规范片段。"""
    standards = read_standards()
    config.ensure_dirs()
    text = (
        "# 个人规范（由 QR 知识库同步）\n\n"
        "以下约定适用于**所有项目与对话**。更新规范后请重新运行 `qr rules --user`。\n\n"
        f"{standards}\n"
    )
    USER_RULES_SNIPPET.write_text(text, encoding="utf-8")
    return USER_RULES_SNIPPET
