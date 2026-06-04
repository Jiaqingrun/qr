"""规范版本沿革：相邻版本 diff，仅输出增/删/改。"""
from __future__ import annotations

import difflib
import re
from typing import Any

from . import db, governance, timeutil

# 开发/测试备注：不写入沿革，并可在清理时删除归档行
_SKIP_CHANGELOG_NOTE = re.compile(r"^(测试|test|debug|冒烟)", re.I)


def _lines(text: str) -> list[str]:
    return (text or "").splitlines()


def _meaningful(line: str) -> bool:
    return bool(line.strip())


def _normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip())


def _block_norm(lines: list[str]) -> str:
    return "\n".join(_normalize_line(ln) for ln in lines if _meaningful(ln))


def _append_line_diff(
    old_chunk: list[str],
    new_chunk: list[str],
    added: list[str],
    deleted: list[str],
) -> None:
    """将 replace 区块拆成逐行增删，避免整块「修改」误报。"""
    if _block_norm(old_chunk) == _block_norm(new_chunk):
        return
    sm = difflib.SequenceMatcher(None, old_chunk, new_chunk, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            for ln in new_chunk[j1:j2]:
                if _meaningful(ln):
                    added.append(ln)
        elif tag == "delete":
            for ln in old_chunk[i1:i2]:
                if _meaningful(ln):
                    deleted.append(ln)
        elif tag == "replace":
            o_sub = old_chunk[i1:i2]
            n_sub = new_chunk[j1:j2]
            if len(o_sub) <= 2 and len(n_sub) <= 2:
                for ln in o_sub:
                    if _meaningful(ln):
                        deleted.append(ln)
                for ln in n_sub:
                    if _meaningful(ln):
                        added.append(ln)
            else:
                _append_line_diff(o_sub, n_sub, added, deleted)


def diff_text(old: str, new: str) -> dict[str, Any]:
    """对比两版正文，返回 added / deleted / modified（modified 仅用于多段实质改写）。"""
    old_lines = _lines(old)
    new_lines = _lines(new)
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    added: list[str] = []
    deleted: list[str] = []
    modified: list[dict[str, str]] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            for ln in new_lines[j1:j2]:
                if _meaningful(ln):
                    added.append(ln)
        elif tag == "delete":
            for ln in old_lines[i1:i2]:
                if _meaningful(ln):
                    deleted.append(ln)
        elif tag == "replace":
            o_chunk = old_lines[i1:i2]
            n_chunk = new_lines[j1:j2]
            o_mean = [ln for ln in o_chunk if _meaningful(ln)]
            n_mean = [ln for ln in n_chunk if _meaningful(ln)]
            # 多行且整体相似度低：保留「修改」块；否则拆成增删
            if len(o_mean) >= 3 and len(n_mean) >= 3:
                ratio = difflib.SequenceMatcher(None, o_mean, n_mean).ratio()
                if ratio < 0.72:
                    modified.append({"before": "\n".join(o_chunk), "after": "\n".join(n_chunk)})
                    continue
            _append_line_diff(o_chunk, n_chunk, added, deleted)

    # 去重（保持顺序）
    def _dedupe(seq: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for ln in seq:
            key = _normalize_line(ln)
            if key in seen:
                continue
            seen.add(key)
            out.append(ln)
        return out

    return {
        "added": _dedupe(added),
        "deleted": _dedupe(deleted),
        "modified": modified,
    }


def _has_substantive_change(diff: dict[str, Any]) -> bool:
    return bool(diff.get("added") or diff.get("deleted") or diff.get("modified"))


def _skip_changelog_note(note: str) -> bool:
    return bool(_SKIP_CHANGELOG_NOTE.match((note or "").strip()))


def skip_changelog_note(note: str) -> bool:
    return _skip_changelog_note(note)


def has_substantive_change(diff: dict[str, Any]) -> bool:
    return _has_substantive_change(diff)


def build_changelog(*, prune_identical: bool = False) -> dict[str, Any]:
    """
    从第二版起，相对上一版列出变更。
    第一版不展示；正文相同、无可展示 diff、测试备注的版本均跳过。
    """
    removed_noise = 0
    removed_identical = 0
    if prune_identical:
        removed_noise = governance.prune_noise_versions()
        pr = governance.prune_redundant_versions()
        removed_identical = int(pr.get("removed") or 0)

    db.init_db()
    with db.session() as conn:
        rows = conn.execute(
            "SELECT id, note, created_at, content FROM standards_versions "
            "ORDER BY created_at ASC, id ASC"
        ).fetchall()

    versions = [dict(r) for r in rows]
    if len(versions) < 2:
        return {
            "changes": [],
            "version_count": len(versions),
            "change_count": 0,
            "pruned_noise": removed_noise,
            "pruned_identical": removed_identical,
        }

    changes: list[dict[str, Any]] = []
    for i in range(1, len(versions)):
        prev_v = versions[i - 1]
        curr_v = versions[i]
        note = (curr_v.get("note") or "").strip()
        if _skip_changelog_note(note):
            continue

        prev_c = prev_v.get("content") or ""
        curr_c = curr_v.get("content") or ""
        if governance.normalize_for_compare(prev_c) == governance.normalize_for_compare(curr_c):
            continue

        diff = diff_text(prev_c, curr_c)
        if not _has_substantive_change(diff):
            continue

        ts = int(curr_v["created_at"])
        changes.append(
            {
                "version_id": int(curr_v["id"]),
                "from_version_id": int(prev_v["id"]),
                "version_index": i + 1,
                "note": note,
                "created_at": ts,
                "timestamp": timeutil.format_local(ts),
                "added": diff["added"],
                "deleted": diff["deleted"],
                "modified": diff["modified"],
            }
        )

    return {
        "changes": list(reversed(changes)),
        "version_count": len(versions),
        "change_count": len(changes),
        "pruned_noise": removed_noise,
        "pruned_identical": removed_identical,
    }
