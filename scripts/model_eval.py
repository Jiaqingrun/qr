#!/usr/bin/env python3
"""Compare models on QR本地知识库 RAG Q&A (scoring in eval_suite)."""
from __future__ import annotations

import json
import time
from pathlib import Path

from qr import config, eval_suite, query


def run_retrieval_baseline(cases: list[dict] | None = None) -> list[dict]:
    """每题只检索一次；与生成模型无关，用于 RAG 回归。"""
    cases = cases or eval_suite.load_cases()
    rows: list[dict] = []
    for case in cases:
        t0 = time.perf_counter()
        hits = query.search(case["q"], k=6)
        search_s = time.perf_counter() - t0
        forbidden = eval_suite.retrieval_forbidden(hits)
        ok = eval_suite.retrieval_ok(hits, case)
        rows.append({
            "case": case["id"],
            "tier": case.get("tier", "core"),
            "question": case["q"],
            "search_s": round(search_s, 2),
            "retrieval_ok": ok,
            "retrieval_forbidden": forbidden,
            "top_hits": [
                {"path": h["path"], "score": round(h["score"], 3)}
                for h in hits[:3]
            ],
        })
        print(
            f"[RAG] {case['id']} retr={ok} forbidden={forbidden} {search_s:.2f}s",
            flush=True,
        )
    return rows


def run_model(
    label: str,
    model: str | None,
    rag_by_case: dict[str, dict] | None = None,
) -> list[dict]:
    results = []
    for case in eval_suite.load_cases():
        rag = (rag_by_case or {}).get(case["id"])
        if rag:
            search_s = rag["search_s"]
            retr_ok = rag["retrieval_ok"]
            retr_forbidden = rag.get("retrieval_forbidden", False)
            top_hits = rag.get("top_hits", [])
        else:
            t0 = time.perf_counter()
            hits = query.search(case["q"], k=6)
            search_s = time.perf_counter() - t0
            retr_ok = eval_suite.retrieval_ok(hits, case)
            retr_forbidden = eval_suite.retrieval_forbidden(hits)
            top_hits = [
                {"path": h["path"], "score": round(h["score"], 3)}
                for h in hits[:3]
            ]
        t1 = time.perf_counter()
        try:
            answer, hits2, _ = query.ask(case["q"], k=6, model=model)
            err = None
        except Exception as e:
            answer, hits2, err = "", [], str(e)
        ask_s = time.perf_counter() - t1
        if hits2 and not rag:
            retr_ok = eval_suite.retrieval_ok(hits2, case)
            retr_forbidden = eval_suite.retrieval_forbidden(hits2)
            top_hits = [
                {"path": h["path"], "score": round(h["score"], 3)}
                for h in hits2[:3]
            ]
        sc = (
            eval_suite.score_answer(answer, case)
            if not err
            else {"must_pass": False, "nice_hits": 0, "nice_total": len(case["nice"])}
        )
        results.append({
            "case": case["id"],
            "tier": case.get("tier", "core"),
            "question": case["q"],
            "model": label,
            "search_s": round(search_s, 2),
            "ask_s": round(ask_s, 2),
            "retrieval_ok": retr_ok,
            "retrieval_forbidden": retr_forbidden,
            "top_hits": top_hits,
            "answer_preview": (answer or err or "")[:600],
            "answer_len": len(answer or ""),
            **sc,
        })
        print(
            f"[{label}] {case['id']} must={sc['must_pass']} {ask_s:.1f}s",
            flush=True,
        )
    return results


def main():
    cfg = config.load_config()
    print("chat_model:", cfg["chat_model"])
    print("deep_model:", cfg["deep_model"])
    print("embed_model:", cfg["embed_model"])
    rag = run_retrieval_baseline()
    rag_map = {r["case"]: r for r in rag}
    out = {
        "chat_model": cfg["chat_model"],
        "deep_model": cfg["deep_model"],
        "rag_baseline": rag,
        "rag_summary": eval_suite.summarize_rag(rag),
        "results": {
            "qwen": run_model("qwen2.5:32b", None, rag_map),
            "deepseek": run_model("deepseek-r1:32b", cfg["deep_model"], rag_map),
        },
    }
    path = config.LOGS_DIR / "model_eval.json"
    config.ensure_dirs()
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", path)


if __name__ == "__main__":
    main()
