"""分块：代码感知 + 符号锚点。"""
from __future__ import annotations

import re
from pathlib import Path

_CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".swift", ".kt", ".java"}

_PY_BOUNDARY = re.compile(
    r"^(?:async\s+def |def |class |@(?:\w+\.)*\w+|export\s+(?:default\s+)?(?:function|class|const)|"
    r"function\s+\w+|interface\s+\w+|type\s+\w+|impl\s+|fn\s+\w+)",
    re.MULTILINE,
)
_SYMBOL_RE = re.compile(
    r"^(?:def|class|async def)\s+(\w+)|^(?:export\s+)?(?:function|class|interface|type)\s+(\w+)",
    re.MULTILINE,
)


def _chunk_plain(text: str, size: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        nl = text.rfind("\n", start + size // 2, end)
        if nl != -1 and end < n:
            end = nl
        chunks.append(text[start:end].strip())
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def _split_code_blocks(text: str, size: int, overlap: int) -> list[str]:
    matches = list(_PY_BOUNDARY.finditer(text))
    if len(matches) < 2:
        return _chunk_plain(text, size, overlap)
    blocks: list[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.append(text[start:end].strip())
    out: list[str] = []
    buf = ""
    for b in blocks:
        if len(b) > size:
            if buf:
                out.append(buf.strip())
                buf = ""
            out.extend(_chunk_plain(b, size, overlap))
            continue
        if len(buf) + len(b) + 1 > size and buf:
            out.append(buf.strip())
            buf = b
        else:
            buf = f"{buf}\n\n{b}".strip() if buf else b
    if buf:
        out.append(buf.strip())
    return [c for c in out if c]


def _symbol_header(path: Path, raw: str) -> str:
    names: list[str] = []
    for m in _SYMBOL_RE.finditer(raw):
        names.append(m.group(1) or m.group(2) or "")
    names = [n for n in names if n][:40]
    if not names:
        return ""
    return f"# 符号索引 · {path.name}\n" + ", ".join(names) + "\n\n"


def chunk_document(path: Path, raw: str, cfg: dict) -> list[str]:
    size = int(cfg.get("chunk_chars", 1200))
    overlap = int(cfg.get("chunk_overlap", 150))
    use_code = bool(cfg.get("code_aware_chunking", True))
    ext = path.suffix.lower()
    header = _symbol_header(path, raw) if ext in _CODE_EXTS else ""
    body = header + raw
    if use_code and ext in _CODE_EXTS:
        parts = _split_code_blocks(body, size, overlap)
    else:
        parts = _chunk_plain(body, size, overlap)
    return parts
