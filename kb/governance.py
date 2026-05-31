from __future__ import annotations

from pathlib import Path

from . import config

_TEMPLATE = config.REPO_ROOT / "standards" / "STANDARDS.md"


def ensure_standards() -> Path:
    config.ensure_dirs()
    if not config.STANDARDS_PATH.exists():
        seed = _TEMPLATE.read_text(encoding="utf-8") if _TEMPLATE.exists() else "# 个人规范\n"
        config.STANDARDS_PATH.write_text(seed, encoding="utf-8")
    return config.STANDARDS_PATH


def read_standards() -> str:
    if config.STANDARDS_PATH.exists():
        return config.STANDARDS_PATH.read_text(encoding="utf-8")
    if _TEMPLATE.exists():
        return _TEMPLATE.read_text(encoding="utf-8")
    return "# 个人规范\n（尚未配置）"


def _rule_mdc(standards: str) -> str:
    return (
        "---\n"
        "description: 个人开发/存储/行为规范（由 kb 生成）\n"
        "globs:\n"
        "alwaysApply: true\n"
        "---\n\n"
        "以下是用户的个人规范，请在本项目的所有开发与文件操作中遵守：\n\n"
        f"{standards}\n"
    )


def _agents_md(standards: str) -> str:
    return (
        "# AGENTS.md\n\n"
        "> 本文件由本地知识库系统 `kb` 根据个人规范生成。AI 助手在本项目中应遵守以下约定。\n\n"
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
