#!/usr/bin/env python3
"""四模型 RAG 问答对比：RAG 基线 + 各模型必达/加分/生成速度，HTML 报告。"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "model_eval", Path(__file__).resolve().parent / "model_eval.py",
)
me = importlib.util.module_from_spec(_spec)
assert _spec.loader
_spec.loader.exec_module(me)

from qr import config, eval_suite, models  # noqa: E402


def run_all_models(rag_map: dict[str, dict]) -> dict[str, list[dict]]:
    catalog = models.ask_catalog()
    all_results: dict[str, list[dict]] = {}
    for entry in catalog:
        mid = entry["id"]
        label = entry["label"]
        print(f"\n=== {label} ({mid}) ===", flush=True)
        all_results[mid] = me.run_model(label, mid, rag_map)
    return all_results


def summarize_model(model_id: str, rows: list[dict]) -> dict:
    n = len(rows) or 1
    must = sum(1 for r in rows if r.get("must_pass"))
    nice = sum(r.get("nice_hits", 0) for r in rows)
    nice_max = sum(r.get("nice_total", 0) for r in rows) or 1
    ask_t = [r["ask_s"] for r in rows]
    core = [r for r in rows if r.get("tier") in ("core", "hard", "trap")]
    core_n = len(core) or 1
    core_must = sum(1 for r in core if r.get("must_pass"))
    return {
        "id": model_id,
        "label": models.model_label(model_id),
        "cases": n,
        "must_pass": must,
        "must_rate": round(100 * must / n, 1),
        "core_must_rate": round(100 * core_must / core_n, 1),
        "nice_rate": round(100 * nice / nice_max, 1),
        "ask_avg": round(sum(ask_t) / n, 2),
        "ask_sum": round(sum(ask_t), 2),
    }


def build_html(payload: dict, out: Path) -> None:
    rag_sum = payload["rag_summary"]
    rag_rows = payload["rag_baseline"]
    sums = payload["summary"]
    rows_detail = payload["results"]
    cases = eval_suite.load_cases()
    case_map = {c["id"]: c["q"] for c in cases}
    tier_map = {c["id"]: c.get("tier", "core") for c in cases}
    max_ask = max(s["ask_avg"] for s in sums) or 1

    def bar(pct: float, color: str) -> str:
        w = max(4, min(100, pct))
        return f'<div class="bar"><i style="width:{w}%;background:{color}"></i></div>'

    rag_table = ""
    for r in rag_rows:
        ok = r.get("retrieval_ok")
        forb = r.get("retrieval_forbidden")
        hits = ", ".join(
            Path(h["path"]).name for h in r.get("top_hits", [])[:2]
        )
        rag_table += (
            f"<tr><td>{r['case']}</td><td>{tier_map.get(r['case'], '')}</td>"
            f"<td class=\"{'pass' if ok else 'fail'}\">{'✓' if ok else '✗'}</td>"
            f"<td class=\"{'fail' if forb else 'pass'}\">{'泄漏' if forb else '—'}</td>"
            f"<td>{r['search_s']:.2f}s</td>"
            f"<td class='prev'>{hits}</td></tr>"
        )

    summary_rows = ""
    colors = ["#6ea8fe", "#7ee0a8", "#ffb86b", "#c9a0ff"]
    for i, s in enumerate(sums):
        c = colors[i % len(colors)]
        speed_pct = 100 * (1 - s["ask_avg"] / max_ask) if max_ask else 0
        summary_rows += f"""
        <tr>
          <td><span class="dot" style="background:{c}"></span>{s['label']}<br><code>{s['id']}</code></td>
          <td>{bar(s['must_rate'], c)}<b>{s['must_rate']}%</b> ({s['must_pass']}/{s['cases']})</td>
          <td>{bar(s['core_must_rate'], c)}<b>{s['core_must_rate']}%</b></td>
          <td>{bar(s['nice_rate'], '#5ad1e0')}<b>{s['nice_rate']}%</b></td>
          <td>{bar(speed_pct, '#ff9ec0')}<b>{s['ask_avg']}s</b><span class="sub">仅生成</span></td>
        </tr>"""

    detail_blocks = ""
    for mid, results in rows_detail.items():
        label = models.model_label(mid)
        detail_blocks += (
            f'<h3>{label}</h3><table class="detail"><thead><tr>'
            f"<th>用例</th><th>类型</th><th>必达</th><th>生成</th><th>预览</th>"
            f"</tr></thead><tbody>"
        )
        for r in results:
            ok = r.get("must_pass")
            q = case_map.get(r["case"], r["case"]).replace("<", "&lt;").replace('"', "&quot;")
            prev = (r.get("answer_preview") or "").replace("<", "&lt;")[:200]
            detail_blocks += (
                f"<tr><td>{r['case']}</td><td>{r.get('tier', '')}</td>"
                f"<td class=\"{'pass' if ok else 'fail'}\">{'✓' if ok else '✗'}</td>"
                f"<td>{r['ask_s']:.1f}s</td>"
                f"<td class='prev' title=\"{q}\">{prev}…</td></tr>"
            )
        detail_blocks += "</tbody></table>"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QR 四模型对比 · {payload['generated']}</title>
<style>
  :root{{--bg:#0e1118;--card:#141824;--text:#f0f3f9;--muted:#8b95ad;--border:#2a3144}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font:14px/1.5 -apple-system,"PingFang SC",sans-serif;background:var(--bg);color:var(--text);padding:24px}}
  h1{{font-size:22px;margin-bottom:6px}}
  .meta{{color:var(--muted);font-size:13px;margin-bottom:24px}}
  .card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px;margin-bottom:20px;overflow-x:auto}}
  h2{{font-size:16px;margin-bottom:14px}}
  h3{{font-size:14px;margin:18px 0 10px;color:#b8c0d4}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th,td{{padding:10px 12px;text-align:left;border-bottom:1px solid var(--border);vertical-align:middle}}
  th{{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.04em}}
  .dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px}}
  .bar{{background:#1a2030;border-radius:6px;height:8px;margin:4px 0;overflow:hidden}}
  .bar i{{display:block;height:100%;border-radius:6px}}
  td b{{margin-left:8px;font-variant-numeric:tabular-nums}}
  .sub{{display:block;font-size:11px;color:var(--muted);margin-top:2px}}
  code{{font-size:11px;color:var(--muted)}}
  .detail td.pass,.rag td.pass{{color:#7ee0a8;font-weight:700}}
  .detail td.fail,.rag td.fail{{color:#ff8fa3;font-weight:700}}
  .prev{{max-width:320px;color:var(--muted);font-size:12px}}
  .legend{{display:flex;flex-wrap:wrap;gap:16px;margin-top:12px;font-size:12px;color:var(--muted)}}
  .note{{background:#1a2030;border-radius:8px;padding:12px;font-size:12px;color:var(--muted);margin-bottom:16px;line-height:1.6}}
</style></head><body>
<h1>QR 本地知识库 · RAG + 四模型对比</h1>
<p class="meta">生成时间 {payload['generated']} · 用例 {payload['case_count']} 条 · 总耗时 {payload['elapsed_s']}s</p>
<div class="note">检索指标在下方「RAG 基线」中只测一次（与模型无关）。模型表只比较<strong>必达命中率</strong>、<strong>核心题必达</strong>、加分项与<strong>生成耗时</strong>。修改索引排除后请运行 <code>qr index</code> 清理评测脚本碎片。</div>
<div class="card">
  <h2>RAG 基线（检索，与模型无关）</h2>
  <p class="meta" style="margin-bottom:12px">命中率 {rag_sum['retrieval_rate']}% ({rag_sum['retrieval_ok']}/{rag_sum['cases']}) · 平均检索 {rag_sum['search_avg']}s · 考题泄漏命中 {rag_sum['forbidden_hits']} 题</p>
  <table class="rag"><thead><tr>
    <th>用例</th><th>类型</th><th>检索</th><th>泄漏</th><th>耗时</th><th>Top 文件</th>
  </tr></thead><tbody>{rag_table}</tbody></table>
  <div class="legend">
    <span>检索：Top 片段来自预期源码/配置</span>
    <span>泄漏：命中 eval_suite / model_eval 等禁入路径</span>
  </div>
</div>
<div class="card">
  <h2>模型对比（生成）</h2>
  <table><thead><tr>
    <th>模型</th><th>必达命中率</th><th>核心+难题必达</th><th>加分项</th><th>平均生成</th>
  </tr></thead><tbody>{summary_rows}</tbody></table>
  <div class="legend">
    <span>必达：答案含关键事实</span>
    <span>核心+难题：除负例外的 core/hard/trap</span>
  </div>
</div>
<div class="card"><h2>逐题明细（生成）</h2>{detail_blocks}</div>
</body></html>"""
    out.write_text(html, encoding="utf-8")


def main():
    config.ensure_dirs()
    cases = eval_suite.load_cases()
    print(f"评测用例: {len(cases)} 条")
    purge = config.load_config().get("index_exclude_path_patterns")
    if purge:
        from qr.indexer import purge_excluded_documents

        pr = purge_excluded_documents(list(purge))
        if pr.get("documents_removed"):
            print(f"已从索引移除 {pr['documents_removed']} 个禁入文档（评测脚本等）")
    t0 = time.time()
    print("\n--- RAG 基线 ---")
    rag = me.run_retrieval_baseline(cases)
    rag_map = {r["case"]: r for r in rag}
    results = run_all_models(rag_map)
    summary = [summarize_model(mid, rows) for mid, rows in results.items()]
    payload = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "case_count": len(cases),
        "elapsed_s": round(time.time() - t0, 1),
        "rag_baseline": rag,
        "rag_summary": eval_suite.summarize_rag(rag),
        "summary": summary,
        "results": results,
    }
    log_dir = config.LOGS_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = log_dir / f"model_compare_{stamp}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path = log_dir / "model_compare_latest.html"
    build_html(payload, html_path)
    print("\n--- RAG ---")
    rs = payload["rag_summary"]
    print(f"检索 {rs['retrieval_rate']}% · 泄漏 {rs['forbidden_hits']} 题")
    print("\n--- 模型（必达 / 核心 / 均生成）---")
    for s in summary:
        print(
            f"{s['label']:22} 必达 {s['must_rate']:5}%  核心 {s['core_must_rate']:5}%  "
            f"生成 {s['ask_avg']:5.1f}s"
        )
    print(f"\nJSON: {json_path}")
    print(f"报告: {html_path}")


if __name__ == "__main__":
    main()
