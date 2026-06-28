from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import config

CASES_PATH = config.QR_HOME / "eval_cases.json"
EXTENDED_CASES_DOC = config.REPO_ROOT / "docs" / "EVAL_EXTENDED_CASES.md"

# core 门禁：含 historical tier 名 hard / trap / negative
CORE_TIERS = frozenset({"core", "hard", "trap", "negative"})

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
        "must": [r"qwen3[-_]embedding"],
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
        "nice": [r"weekly", r"eval", r"web", r"com\.qr"],
        "expect_paths": ["cli.py"],
    },
    {
        "id": "context_cfg",
        "tier": "core",
        "q": "QR本地知识库 config.json 里 context_tokens 和 deep_context_tokens 分别是多少？",
        "must": [r"32768", r"131072"],
        "nice": ["context_tokens", "deep_context_tokens"],
        "expect_paths": [
            "config.json", "qr-config", "config.py", "indexer.py", "ollama_client.py",
        ],
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
        "expect_paths": [
            "config.json", "qr-config", "config.py", "indexer.py", "cli.py", "web.py",
        ],
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

EXTENDED_BUILTIN_CASES = [
    {
        "id": "oral_port",
        "tier": "extended",
        "category": "oral",
        "q": "知识库网页默认几号端口来着？",
        "must": [],
        "nice": [],
        "expect_paths": ["config.json", "config.py", "web.py", "/.qr/"],
        "doc": "口语问法；期望 config / web 中 web_port 8765",
    },
    {
        "id": "oral_data_dir",
        "tier": "extended",
        "category": "oral",
        "q": "运行数据放哪个隐藏目录？就本机知识库那个。",
        "must": [],
        "nice": [],
        "expect_paths": ["config.py", "/.qr/", "QR_HOME", "standards"],
        "doc": "口语问法；期望 ~/.qr / QR_HOME",
    },
    {
        "id": "oral_conda",
        "tier": "extended",
        "category": "oral",
        "q": "这个知识库项目规定用哪个 conda 环境名？",
        "must": [],
        "nice": [],
        "expect_paths": ["STANDARDS", "standards.md", "README", "AGENTS"],
        "doc": "口语问法；期望 standards 中 conda 环境 qr",
    },
    {
        "id": "narrative_schedule",
        "tier": "extended",
        "category": "narrative",
        "q": "装了 schedule 之后，每周大概会自动跑哪些维护任务？",
        "must": [],
        "nice": [],
        "expect_paths": ["cli.py", "schedule", "standards", "EVOLUTION"],
        "doc": "跨文件叙事；期望 schedule / weekly / update 相关说明",
    },
    {
        "id": "narrative_retrieval_plan",
        "tier": "extended",
        "category": "narrative",
        "q": "检索升级计划里什么时候才考虑上 HyDE 或多查询？",
        "must": [],
        "nice": [],
        "expect_paths": ["RETRIEVAL_UPGRADE", "RETRIEVAL", "query.py"],
        "doc": "跨文件叙事；期望 RETRIEVAL_UPGRADE_PLAN 触发条件",
    },
    {
        "id": "decision_milestone",
        "tier": "extended",
        "category": "decision",
        "q": "里程碑结束至少要记一条什么类型的日志？",
        "must": [],
        "nice": [],
        "expect_paths": ["STANDARDS", "standards.md", "USE_CASES", "decision"],
        "doc": "决策/规范检索；期望 qr log --type decision",
    },
]


def case_tier_group(case: dict) -> str:
    """core 门禁题 vs extended 扩展题（不阻断发布）。"""
    tier = str(case.get("tier") or "core").strip().lower()
    return "core" if tier in CORE_TIERS else "extended"


def load_cases(*, include_extended: bool = True) -> list[dict]:
    base = list(BUILTIN_CASES)
    if include_extended:
        base.extend(EXTENDED_BUILTIN_CASES)
    if not CASES_PATH.exists():
        if not include_extended:
            return [c for c in base if case_tier_group(c) == "core"]
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
    out = list(by_id.values())
    if not include_extended:
        out = [c for c in out if case_tier_group(c) == "core"]
    return out


def filter_cases_by_group(cases: list[dict], group: str) -> list[dict]:
    g = (group or "").strip().lower()
    return [c for c in cases if case_tier_group(c) == g]


def extended_cases_reference() -> list[dict]:
    """扩展题说明（期望路径与 doc 字段）。"""
    cases = list(EXTENDED_BUILTIN_CASES)
    if CASES_PATH.exists():
        try:
            extra = json.loads(CASES_PATH.read_text(encoding="utf-8"))
            custom = extra.get("cases") if isinstance(extra, dict) else []
            if isinstance(custom, list):
                for c in custom:
                    if c.get("id") and case_tier_group(c) == "extended":
                        cases.append(c)
        except json.JSONDecodeError:
            pass
    return cases


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
    n = len(rows)
    if n == 0:
        return {
            "cases": 0,
            "retrieval_ok": 0,
            "retrieval_rate": 0.0,
            "forbidden_hits": 0,
            "search_avg": 0.0,
        }
    ok = sum(1 for r in rows if r.get("retrieval_ok"))
    forbidden = sum(1 for r in rows if r.get("retrieval_forbidden"))
    return {
        "cases": n,
        "retrieval_ok": ok,
        "retrieval_rate": round(100 * ok / n, 1),
        "forbidden_hits": forbidden,
        "search_avg": round(sum(r.get("search_s", 0) for r in rows) / n, 2),
    }


def summarize_rag_split(rows: list[dict]) -> dict[str, dict]:
    """按 core / extended 分栏汇总（M4-1）。"""
    core_rows = [r for r in rows if case_tier_group({"tier": r.get("tier", "core")}) == "core"]
    ext_rows = [r for r in rows if case_tier_group({"tier": r.get("tier", "core")}) == "extended"]
    return {
        "all": summarize_rag(rows),
        "core": summarize_rag(core_rows),
        "extended": summarize_rag(ext_rows),
    }


def model_pass_counts(data: dict) -> dict[str, int]:
    """统计各模型评测通过题数（results 下任意 key）。"""
    out: dict[str, int] = {}
    for key, rows in (data.get("results") or {}).items():
        if isinstance(rows, list):
            out[str(key)] = sum(1 for r in rows if r.get("must_pass"))
    return out


def eval_case_total(data: dict) -> int:
    """单次评测的题量（取各模型结果长度的最大值）。"""
    results = data.get("results") or {}
    lengths = [len(rows) for rows in results.values() if isinstance(rows, list)]
    return max(lengths) if lengths else 1


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
        scores = model_pass_counts(data)
        return {
            "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)),
            "scores": scores,
            "qwen": scores.get("qwen", 0),
            "deepseek": scores.get("deepseek", 0),
            "total": eval_case_total(data),
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
        keys = set(series[0].get("scores", {})) | set(series[1].get("scores", {}))
        delta = {
            k: series[0]["scores"].get(k, 0) - series[1]["scores"].get(k, 0)
            for k in sorted(keys)
        }
    return {"series": series, "delta": delta, "cases_path": str(CASES_PATH)}
