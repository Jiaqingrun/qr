from __future__ import annotations

import json
import time
from pathlib import Path

from . import config

CASES_PATH = config.QR_HOME / "eval_cases.json"

BUILTIN_CASES = [
    {
        "id": "port",
        "q": "QR本地知识库 Web 服务默认监听哪个端口？只答端口号。",
        "must": [r"8765"],
        "nice": ["web_port", "config"],
    },
    {
        "id": "embed",
        "q": "QR本地知识库当前配置使用的向量嵌入模型名称是什么？",
        "must": [r"bge[-_]m3"],
        "nice": ["embed_model"],
    },
    {
        "id": "chat_tables",
        "q": "QR本地知识库里 chat_sessions 和 chat_messages 两张表分别存什么？用一句话说明各自作用。",
        "must": [r"session", r"消息|message|对话"],
        "nice": ["chat_sessions", "chat_messages"],
    },
    {
        "id": "schedule",
        "q": "运行 QR本地知识库 schedule install 会安装哪些 launchd 后台任务？列出任务 label 或名称。",
        "must": [r"tracker", r"cursor", r"auto"],
        "nice": [r"weekly", r"web", r"com.qr"],
    },
    {
        "id": "context_cfg",
        "q": "QR本地知识库 config.json 里 context_tokens 和 deep_context_tokens 分别是多少？",
        "must": [r"32768", r"131072"],
        "nice": ["context_tokens", "deep_context_tokens"],
    },
    {
        "id": "negative",
        "q": "ai-story-forge 项目的前端框架用的是 Next.js 还是 Vue？",
        "must": [r"不知道|没有|无法|未|找不到|空|无相关|未能"],
        "nice": [],
        "negative": True,
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
