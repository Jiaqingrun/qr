#!/usr/bin/env python3
"""Compare qwen2.5:32b vs deepseek-r1:32b on QR本地知识库 RAG Q&A."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from qr import config, eval_suite, query

CASES = eval_suite.BUILTIN_CASES


def score_answer(text: str, case: dict) -> dict:
    t = text.lower()
    must_ok = all(re.search(p, text, re.I) for p in case["must"])
    nice_hits = sum(1 for p in case["nice"] if re.search(p, text, re.I))
    if case.get("negative"):
        # 负例题允许复述问题中的备选项，只要结论是“无法确定/不知道”即可通过；
        # 仅在出现明确断言时（例如“是 Vue/是 Next.js”）判失败。
        assertive = re.search(
            r"(是|为|采用|使用).{0,12}(next\.?js|vue)(?!\s*还是)",
            text,
            re.I,
        )
        must_ok = must_ok and not bool(assertive)
    return {"must_pass": must_ok, "nice_hits": nice_hits, "nice_total": len(case["nice"])}


def retrieval_ok(hits: list[dict], case: dict) -> bool:
    if not hits:
        return False
    paths = " ".join(h["path"] for h in hits[:3]).lower()
    if case["id"] == "negative":
        return True
    if case["id"] in ("port", "embed", "context_cfg"):
        return any(
            k in paths
            for k in ("config.json", "/.qr/", "/qr/config.py", "qr/config.py", "qr-config")
        )
    if case["id"] == "chat_tables":
        return "chat" in paths or "db.py" in paths or "web.py" in paths
    if case["id"] == "schedule":
        return "cli.py" in paths
    return True


def run_model(label: str, model: str | None) -> list[dict]:
    cfg = config.load_config()
    results = []
    for case in eval_suite.load_cases():
        t0 = time.perf_counter()
        hits = query.search(case["q"], k=6)
        t_search = time.perf_counter() - t0
        t1 = time.perf_counter()
        try:
            answer, hits2, _ = query.ask(case["q"], k=6, model=model)
            err = None
        except Exception as e:
            answer, hits2, err = "", hits, str(e)
        t_ask = time.perf_counter() - t1
        hits = hits2 or hits
        sc = score_answer(answer, case) if not err else {"must_pass": False, "nice_hits": 0, "nice_total": len(case["nice"])}
        results.append({
            "case": case["id"],
            "question": case["q"],
            "model": label,
            "search_s": round(t_search, 2),
            "ask_s": round(t_ask, 2),
            "retrieval_ok": retrieval_ok(hits, case),
            "top_hits": [{"path": h["path"], "score": round(h["score"], 3)} for h in hits[:3]],
            "answer_preview": (answer or err or "")[:600],
            "answer_len": len(answer or ""),
            **sc,
        })
        print(f"[{label}] {case['id']} must={sc['must_pass']} {t_ask:.1f}s", flush=True)
    return results


def main():
    cfg = config.load_config()
    print("chat_model:", cfg["chat_model"])
    print("deep_model:", cfg["deep_model"])
    print("embed_model:", cfg["embed_model"])
    print("docs/chunks from index...")
    out = {
        "chat_model": cfg["chat_model"],
        "deep_model": cfg["deep_model"],
        "results": {
            "qwen": run_model("qwen2.5:32b", None),
            "deepseek": run_model("deepseek-r1:32b", cfg["deep_model"]),
        },
    }
    path = Path.home() / ".qr" / "logs" / "model_eval.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", path)


if __name__ == "__main__":
    main()
