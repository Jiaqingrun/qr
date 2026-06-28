"""规范修订预览与待确认队列（M6-1）。"""
from __future__ import annotations

import difflib
import json
import re
from typing import Any

from . import config, db, governance, standards_changelog

PENDING_PATH = config.QR_HOME / "standards_pending.json"
_STATE_PENDING = "standards_revision_pending"

_SECTION_HEADER = re.compile(r"^##\s*([一二三四五六])、", re.MULTILINE)


def needs_confirmation(cfg: dict | None = None) -> bool:
    cfg = cfg or config.load_config()
    return bool(cfg.get("standards_auto_confirm", True))


def section_boundaries(text: str) -> list[dict[str, str]]:
    """§一～§六 章节标题行（用于 diff 预览高亮）。"""
    out: list[dict[str, str]] = []
    for ln in (text or "").splitlines():
        m = _SECTION_HEADER.match(ln.strip())
        if m:
            out.append({"section": m.group(1), "title": ln.strip()})
    return out


def diff_preview(old: str, new: str) -> dict[str, Any]:
    """结构化 diff + unified diff + 章节边界。"""
    diff = standards_changelog.diff_text(old, new)
    unified = list(
        difflib.unified_diff(
            (old or "").splitlines(),
            (new or "").splitlines(),
            fromfile="当前规范",
            tofile="修订草案",
            lineterm="",
            n=3,
        )
    )
    return {
        **diff,
        "sections": section_boundaries(new),
        "unified": unified,
        "has_change": standards_changelog.has_substantive_change(diff),
    }


def load_pending() -> dict[str, Any] | None:
    if not PENDING_PATH.is_file():
        return None
    try:
        data = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or not data.get("after"):
        return None
    return data


def clear_pending() -> bool:
    existed = PENDING_PATH.is_file()
    try:
        if existed:
            PENDING_PATH.unlink()
    except OSError:
        pass
    try:
        with db.session() as conn:
            db.set_state(conn, _STATE_PENDING, "")
    except Exception:
        pass
    return existed


def store_pending(
    *,
    before: str,
    after: str,
    note: str,
    period: str = "week",
    from_conversations: bool = False,
    source: str = "revise",
) -> dict[str, Any]:
    """写入待确认修订；不修改当前生效规范。"""
    config.ensure_dirs()
    payload: dict[str, Any] = {
        "scope": "global",
        "before": before,
        "after": after,
        "note": note,
        "period": period,
        "from_conversations": from_conversations,
        "source": source,
        "created_at": db.now(),
        "diff": diff_preview(before, after),
    }
    PENDING_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        with db.session() as conn:
            db.set_state(
                conn,
                _STATE_PENDING,
                json.dumps(
                    {"scope": "global", "created_at": payload["created_at"], "note": note},
                    ensure_ascii=False,
                ),
            )
    except Exception:
        pass
    return payload


def confirm_pending(*, note: str = "") -> tuple[str, bool]:
    """应用待确认修订。返回 (正文, 是否新建归档版)。"""
    pending = load_pending()
    if not pending:
        raise ValueError("没有待确认的规范修订")
    base_note = (pending.get("note") or "自动修订").strip()
    if base_note.startswith("待确认："):
        base_note = base_note[4:].strip()
    label = f"已确认：{base_note}"
    extra = (note or "").strip()
    if extra:
        label = f"{label}（{extra}）"
    recorded = governance.save_standards(pending["after"], note=label)
    clear_pending()
    return pending["after"], recorded


def reject_pending() -> bool:
    return clear_pending()


def finish_global_revision(
    proposed: str,
    current: str,
    note: str,
    *,
    confirm: bool | None = None,
    period: str = "week",
    from_conversations: bool = False,
    source: str = "revise",
) -> tuple[str, bool, bool, bool]:
    """
    根据配置决定直接保存或进入待确认队列。

    返回 (正文, 是否新建归档版, 是否有实质变更, 是否进入待确认)。
    """
    changed = governance.normalize_for_compare(current) != governance.normalize_for_compare(
        proposed
    )
    if not changed:
        return proposed, False, False, False

    need = needs_confirmation() if confirm is None else confirm
    if need:
        store_pending(
            before=current,
            after=proposed,
            note=f"待确认：{note}",
            period=period,
            from_conversations=from_conversations,
            source=source,
        )
        return proposed, False, True, True

    recorded = governance.save_standards(proposed, note=note)
    clear_pending()
    return proposed, recorded, True, False


def format_cli_diff(diff: dict[str, Any], *, max_lines: int = 80) -> str:
    """终端用 unified diff 文本。"""
    lines = list(diff.get("unified") or [])
    if not lines:
        added = diff.get("added") or []
        deleted = diff.get("deleted") or []
        if not added and not deleted:
            return "（无实质 diff）"
        parts: list[str] = []
        for ln in deleted[:40]:
            parts.append(f"- {ln}")
        for ln in added[:40]:
            parts.append(f"+ {ln}")
        return "\n".join(parts)
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... 另有 {len(diff.get('unified') or []) - max_lines} 行"]
    return "\n".join(lines)
