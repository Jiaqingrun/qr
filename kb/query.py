from __future__ import annotations

import numpy as np

from . import db
from .ollama_client import Ollama
from .vectors import cosine_topk, from_blob


def _load_chunks():
    with db.session() as conn:
        rows = conn.execute(
            "SELECT c.id, c.text, c.embedding, d.path, d.project "
            "FROM chunks c JOIN documents d ON c.doc_id=d.id"
        ).fetchall()
    if not rows:
        return [], np.empty((0, 0), dtype=np.float32)
    matrix = np.vstack([from_blob(r["embedding"]) for r in rows])
    return rows, matrix


def search(question: str, k: int = 6) -> list[dict]:
    rows, matrix = _load_chunks()
    if not rows:
        return []
    ol = Ollama()
    qvec = ol.embed(question)
    hits = cosine_topk(qvec, matrix, k)
    out = []
    for idx, score in hits:
        r = rows[idx]
        out.append({"score": score, "path": r["path"], "project": r["project"],
                    "text": r["text"]})
    return out


SYSTEM = (
    "你是用户的本地知识库助手。只依据提供的【上下文】回答问题，"
    "引用来源文件路径。如果上下文中没有答案，明确说明不知道，不要编造。用简体中文回答。"
)


def ask(question: str, k: int = 6) -> tuple[str, list[dict]]:
    hits = search(question, k)
    if not hits:
        return ("知识库为空或没有检索到相关内容。请先把项目放入索引目录并运行 `kb index`。", [])
    context = "\n\n".join(
        f"[来源 {i+1}] {h['path']}\n{h['text']}" for i, h in enumerate(hits)
    )
    prompt = f"【上下文】\n{context}\n\n【问题】\n{question}\n\n请基于上下文回答，并标注引用的来源编号。"
    answer = Ollama().generate(prompt, system=SYSTEM)
    return answer, hits
