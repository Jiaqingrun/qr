from __future__ import annotations

import re
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
    r"^##\s*[六七八九十]、|问答界面|检索内容增加|规范页面根据日常",
    re.MULTILINE | re.IGNORECASE,
)

_REVISION_SYSTEM = (
    "你是用户的个人开发/存储/行为规范编辑助手。"
    "规范只描述长期习惯：目录、conda、Git、AI 协作、复盘，不描述某个软件界面长什么样。"
    "禁止把 Cursor 对话主题、Web 前端改版、颜色样式、单次任务过程写成规范条目。"
    "保持五个主章节（一至五），不要新增第六、七、八章。"
    "命令行写 qr，数据目录写 ~/.qr，不要写 kb。"
    "只输出完整 Markdown 正文，从「# 个人开发 / 存储 / 行为规范」一行开始，不要解释、不要复述题目。"
)


def _seed_content() -> str:
    if _TEMPLATE.exists():
        return _TEMPLATE.read_text(encoding="utf-8")
    return "# 个人开发 / 存储 / 行为规范\n（尚未配置）\n"


def ensure_standards() -> Path:
    config.ensure_dirs()
    db.init_db()
    if not config.STANDARDS_PATH.exists():
        config.STANDARDS_PATH.write_text(_seed_content(), encoding="utf-8")
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


def save_standards(content: str, note: str = "手动更新") -> None:
    config.ensure_dirs()
    db.init_db()
    cleaned = _sanitize_standards_output(content)
    if not _is_valid_standards(cleaned):
        raise ValueError("规范正文格式无效，已拒绝保存（请检查是否混入 UI 描述或 prompt 残留）")
    config.STANDARDS_PATH.write_text(cleaned, encoding="utf-8")
    with db.session() as conn:
        conn.execute(
            "INSERT INTO standards_versions(content,note,created_at) VALUES(?,?,?)",
            (cleaned, note, db.now()))


def restore_standards_from_template(note: str = "恢复为标准模板") -> str:
    """用仓库内标准模板覆盖当前生效规范。"""
    content = _seed_content()
    config.ensure_dirs()
    db.init_db()
    config.STANDARDS_PATH.write_text(content, encoding="utf-8")
    with db.session() as conn:
        conn.execute(
            "INSERT INTO standards_versions(content,note,created_at) VALUES(?,?,?)",
            (content, note, db.now()),
        )
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
    if content.strip() == current.strip():
        return content
    label = note or f"设为生效（源自版本 #{vid}）"
    config.ensure_dirs()
    db.init_db()
    config.STANDARDS_PATH.write_text(content, encoding="utf-8")
    with db.session() as conn:
        conn.execute(
            "INSERT INTO standards_versions(content,note,created_at) VALUES(?,?,?)",
            (content, label, db.now()),
        )
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
    required = ("## 一、", "## 二、", "## 三、", "## 四、", "## 五、")
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


def revise_from_behavior(period: str = "week") -> str:
    """基于近期行为数据，让本地模型对规范做适度增减，保存为新版本。"""
    from . import summary
    from .ollama_client import Ollama

    start, end = summary._window(period)
    with db.session() as conn:
        digest = _digest_for_revision(conn, start, end)
    current = read_standards()
    prompt = (
        f"# 当前生效的个人规范（请在此基础上微调，勿推翻结构）\n\n{current}\n\n"
        f"# 最近一个{period}的行为摘要（仅供发现习惯偏差，不要把具体任务/界面需求写进规范）\n\n"
        f"{digest}\n\n"
        "请输出修订后的**完整**规范 Markdown。"
    )
    raw = Ollama().generate(
        prompt,
        system=_REVISION_SYSTEM,
        strip_think=True,
    )
    new = _sanitize_standards_output(raw)
    if not _is_valid_standards(new):
        raise ValueError(
            "自动修订结果未通过校验（可能混入了 UI/对话内容或章节结构错误），"
            "未写入文件。请手动编辑，或先执行 qr standards --restore 恢复模板。"
        )
    save_standards(new, note=f"根据最近{period}行为自动修订")
    return new


def _rule_mdc(standards: str) -> str:
    return (
        "---\n"
        "description: 个人开发/存储/行为规范（由 QR本地知识库 生成）\n"
        "globs:\n"
        "alwaysApply: true\n"
        "---\n\n"
        "以下是用户的个人规范，请在本项目的所有开发与文件操作中遵守：\n\n"
        f"{standards}\n"
    )


def _agents_md(standards: str) -> str:
    return (
        "# AGENTS.md\n\n"
        "> 本文件由 QR本地知识库 根据个人规范生成。AI 助手在本项目中应遵守以下约定。\n\n"
        f"{standards}\n"
    )


def generate_rules(target: Path) -> list[Path]:
    standards = read_standards()
    target = target.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    rules_dir = target / ".cursor" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    rule_path = rules_dir / "00-personal-standards.mdc"
    rule_path.write_text(_rule_mdc(standards), encoding="utf-8")
    written.append(rule_path)

    agents_path = target / "AGENTS.md"
    agents_path.write_text(_agents_md(standards), encoding="utf-8")
    written.append(agents_path)
    return written
