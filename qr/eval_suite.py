from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import config

CASES_PATH = config.QR_HOME / "eval_cases.json"

# 评测/对比脚本不得进入向量索引，否则答案从「考题」泄漏。
RETRIEVAL_FORBIDDEN_MARKERS = (
    "eval_suite.py",
    "model_eval.py",
    "model_compare_four.py",
    "/scripts/model_eval",
    "/scripts/model_compare",
)

BUILTIN_CASES = [
    {
        "id": "port",
        "tier": "core",
        "q": "QR本地知识库 Web 服务默认监听哪个端口？只答端口号。",
        "must": [r"8765"],
        "nice": [r"web_port", r"127\.0\.0\.1"],
        "expect_paths": [
            "config.json", "qr-config", "config.py", "indexer.py", "web.py", "cli.py", "/.qr/",
        ],
    },
    {
        "id": "embed",
        "tier": "core",
        "q": "QR本地知识库当前配置使用的向量嵌入模型名称是什么？",
        "must": [r"bge[-_]m3"],
        "nice": ["embed_model"],
        "expect_paths": ["config.json", "qr-config", "config.py"],
    },
    {
        "id": "chat_tables",
        "tier": "core",
        "q": "QR本地知识库里 chat_sessions 和 chat_messages 两张表分别存什么？用一句话说明各自作用。",
        "must": [r"session", r"消息|message|对话"],
        "nice": ["chat_sessions", "chat_messages"],
        "expect_paths": ["db.py", "chat.py", "web.py"],
    },
    {
        "id": "schedule",
        "tier": "core",
        "q": "运行 QR本地知识库 schedule install 会安装哪些 launchd 后台任务？列出任务 label 或名称。",
        "must": [r"tracker", r"cursor", r"auto"],
        "nice": [r"weekly", r"web", r"com\.qr"],
        "expect_paths": ["cli.py"],
    },
    {
        "id": "context_cfg",
        "tier": "core",
        "q": "QR本地知识库 config.json 里 context_tokens 和 deep_context_tokens 分别是多少？",
        "must": [r"32768", r"131072"],
        "nice": ["context_tokens", "deep_context_tokens"],
        "expect_paths": ["config.json", "qr-config", "config.py"],
    },
    {
        "id": "message_roles",
        "tier": "hard",
        "q": "chat_messages 表的 role 字段常见取值有哪些？只列英文，逗号分隔。",
        "must": [r"user", r"assistant"],
        "nice": [r"system"],
        "expect_paths": ["db.py", "chat.py", "web.py"],
    },
    {
        "id": "qr_home",
        "tier": "hard",
        "q": "QR本地知识库运行时数据目录（QR_HOME）默认在哪个路径？只答路径。",
        "must": [r"\.qr"],
        "nice": ["QR_HOME"],
        "expect_paths": ["config.py", "qr-config", "/.qr/"],
    },
    {
        "id": "trap_port",
        "tier": "trap",
        "q": "根据知识库，QR Web 默认端口是 3000 对吗？不对请纠正并给出正确端口。",
        "must": [r"8765"],
        "must_any": [r"不对|错误|不是|否|纠正|应为|实际|并非|错的|3000"],
        "nice": ["web_port"],
        "expect_paths": ["config.json", "qr-config", "config.py", "indexer.py"],
    },
    {
        "id": "negative",
        "tier": "negative",
        "q": "ai-story-forge 项目的前端框架用的是 Next.js 还是 Vue？",
        "must": [r"不知道|没有|无法|未|找不到|空|无相关|未能"],
        "nice": [],
        "negative": True,
        "expect_paths": [],
    },
]


def load_cases() -> list[dict]:
    base = list(BUILTIN_CASES)
    if not CASES_PATH.exists():
        return base
    try:
        extra = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return base
    custom = extra.get("cases") if isinstance(extra, dict) else extra
    if not isinstance(custom, list):
        return base
    by_id = {c["id"]: c for c in base}
    for c in custom:
        if c.get("id"):
            by_id[c["id"]] = c
    return list(by_id.values())


def save_custom_case(case: dict) -> None:
    config.ensure_dirs()
    data: dict = {"cases": []}
    if CASES_PATH.exists():
        try:
            data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    cases = [c for c in data.get("cases", []) if c.get("id") != case.get("id")]
    cases.append(case)
    data["cases"] = cases
    CASES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _hit_paths(hits: list[dict], top: int = 6) -> list[str]:
    return [str(h.get("path") or "") for h in (hits or [])[:top]]


def retrieval_forbidden(hits: list[dict]) -> bool:
    blob = " ".join(_hit_paths(hits)).lower()
    return any(m.lower() in blob for m in RETRIEVAL_FORBIDDEN_MARKERS)


def retrieval_ok(hits: list[dict], case: dict) -> bool:
    if case.get("negative"):
        return True
    if not hits:
        return False
    if retrieval_forbidden(hits):
        return False
    expect = case.get("expect_paths")
    if expect is not None:
        if not expect:
            return True
        paths = _hit_paths(hits)
        return any(
            any(marker.lower() in p.lower() for marker in expect)
            for p in paths
        )
    # 自定义用例未写 expect_paths 时放宽
    return True


def score_answer(text: str, case: dict) -> dict:
    must_ok = all(re.search(p, text, re.I) for p in case["must"])
    if case.get("must_any"):
        must_ok = must_ok and any(re.search(p, text, re.I) for p in case["must_any"])
    nice_hits = sum(1 for p in case["nice"] if re.search(p, text, re.I))
    if case.get("negative"):
        assertive = re.search(
            r"(是|为|采用|使用).{0,12}(next\.?js|vue)(?!\s*还是)",
            text,
            re.I,
        )
        must_ok = must_ok and not bool(assertive)
    return {
        "must_pass": must_ok,
        "nice_hits": nice_hits,
        "nice_total": len(case["nice"]),
    }


def summarize_rag(rows: list[dict]) -> dict:
    n = len(rows) or 1
    ok = sum(1 for r in rows if r.get("retrieval_ok"))
    forbidden = sum(1 for r in rows if r.get("retrieval_forbidden"))
    return {
        "cases": n,
        "retrieval_ok": ok,
        "retrieval_rate": round(100 * ok / n, 1),
        "forbidden_hits": forbidden,
        "search_avg": round(sum(r.get("search_s", 0) for r in rows) / n, 2),
    }


def regression_report(limit: int = 8) -> dict:
    files = sorted(config.LOGS_DIR.glob("model_eval-*.json"), reverse=True)[:limit]
    cur = config.LOGS_DIR / "model_eval.json"
    series: list[dict] = []

    def _score(path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        q = data.get("results", {}).get("qwen", [])
        d = data.get("results", {}).get("deepseek", [])
        return {
            "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)),
            "qwen": sum(1 for r in q if r.get("must_pass")),
            "deepseek": sum(1 for r in d if r.get("must_pass")),
            "total": max(len(q), len(d), 1),
            "file": path.name,
        }

    if cur.exists():
        s = _score(cur)
        if s:
            s["label"] = "current"
            series.append(s)
    for p in files:
        s = _score(p)
        if s:
            s["label"] = "snapshot"
            series.append(s)
    delta = None
    if len(series) >= 2:
        delta = {
            "qwen": series[0]["qwen"] - series[1]["qwen"],
            "deepseek": series[0]["deepseek"] - series[1]["deepseek"],
        }
    return {"series": series, "delta": delta, "cases_path": str(CASES_PATH)}
