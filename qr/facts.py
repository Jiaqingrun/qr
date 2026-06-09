from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import config

FACTS_PATH = config.QR_HOME / "facts.json"

_PATTERNS = [
    (re.compile(r"(?:embed_model|嵌入模型)[:：\s]+[`\"]?([a-z0-9._-]+)[`\"]?", re.I), "embed_model"),
    (re.compile(r"(?:web_port|端口)[:：\s]+(\d{2,5})", re.I), "web_port"),
    (re.compile(r"context_tokens[:：\s]+(\d+)", re.I), "context_tokens"),
    (re.compile(r"deep_context_tokens[:：\s]+(\d+)", re.I), "deep_context_tokens"),
    (re.compile(r"(?:默认|监听).{0,12}端口[:：\s]+(\d{2,5})", re.I), "web_port"),
]


def _load() -> dict:
    if not FACTS_PATH.exists():
        return {"facts": [], "updated_at": 0}
    try:
        return json.loads(FACTS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"facts": [], "updated_at": 0}


def _save(data: dict) -> None:
    config.ensure_dirs()
    data["updated_at"] = int(time.time())
    FACTS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_facts(project: str | None = None) -> list[dict]:
    data = _load()
    facts = data.get("facts", [])
    if project:
        pl = project.lower()
        facts = [f for f in facts if not f.get("project") or pl in str(f.get("project", "")).lower()]
    return sorted(facts, key=lambda x: x.get("updated_at", 0), reverse=True)


def add_fact(key: str, value: str, project: str | None = None, source: str = "manual") -> dict:
    data = _load()
    facts = data.get("facts", [])
    now = int(time.time())
    for f in facts:
        if f.get("key") == key and (not project or f.get("project") == project):
            f["value"] = value
            f["project"] = project
            f["source"] = source
            f["updated_at"] = now
            _save(data)
            return f
    row = {"key": key, "value": value, "project": project, "source": source, "updated_at": now}
    facts.append(row)
    data["facts"] = facts
    _save(data)
    return row


def delete_fact(key: str, project: str | None = None) -> bool:
    data = _load()
    before = len(data.get("facts", []))
    data["facts"] = [
        f for f in data.get("facts", [])
        if not (f.get("key") == key and (not project or f.get("project") == project))
    ]
    _save(data)
    return len(data["facts"]) < before


def extract_from_text(text: str, project: str | None = None) -> list[dict]:
    added = []
    for pat, key in _PATTERNS:
        m = pat.search(text)
        if m:
            added.append(add_fact(key, m.group(1), project=project, source="auto_extract"))
    return added


_RETRIEVAL_TRIGGERS: list[tuple[str, list[str]]] = [
    ("web_port", [r"端口", r"\bport\b", r"8765", r"3000", r"web\s*服务", r"监听"]),
    ("embed_model", [r"embed", r"嵌入", r"qwen3[-_]embedding", r"向量模型", r"向量嵌入"]),
    ("context_tokens", [r"context_tokens", r"上下文.*token"]),
    ("deep_context_tokens", [r"deep_context"]),
    ("chat_model", [r"chat_model", r"问答模型", r"推理模型"]),
]


def _matched_fact_keys(question: str) -> list[str]:
    keys: list[str] = []
    for key, patterns in _RETRIEVAL_TRIGGERS:
        if any(re.search(p, question, re.I) for p in patterns):
            keys.append(key)
    return keys


def retrieval_hits(question: str, project: str | None = None) -> list[dict]:
    """配置类问题：把稳定事实合成检索命中，置顶供 RAG 引用。"""
    keys = _matched_fact_keys(question)
    if not keys:
        return []
    by_key = {f["key"]: f for f in list_facts(project)}
    cfg = config.load_config()
    cfg_path = str(config.CONFIG_PATH.resolve())
    hits: list[dict] = []
    for key in keys:
        row = by_key.get(key)
        if row:
            val = row["value"]
            proj = row.get("project") or "QR"
        elif key in cfg:
            val = cfg[key]
            proj = "QR"
        else:
            continue
        hits.append({
            "chunk_id": 0,
            "score": 0.96,
            "path": cfg_path,
            "project": proj,
            "text": f"[稳定事实] {key}: {val}（~/.qr/config.json / facts.json）",
            "source_type": "config",
            "fact": True,
        })
    return hits[:4]


def prompt_block(project: str | None = None, limit: int = 20) -> str:
    facts = list_facts(project)[:limit]
    if not facts:
        return ""
    lines = ["【稳定事实（长期记忆）】"]
    for f in facts:
        proj = f" · {f['project']}" if f.get("project") else ""
        lines.append(f"- {f['key']}: {f['value']}{proj}")
    return "\n".join(lines)


def sync_from_config() -> list[dict]:
    from . import schedule_service

    cfg = config.load_config()
    added = []
    added.append(add_fact("embed_model", str(cfg.get("embed_model")), project="QR", source="config"))
    added.append(add_fact("chat_model", str(cfg.get("chat_model")), project="QR", source="config"))
    added.append(add_fact("web_port", str(cfg.get("web_port", 8765)), project="QR", source="config"))
    added.append(add_fact("context_tokens", str(cfg.get("context_tokens")), project="QR", source="config"))
    added.append(add_fact("deep_context_tokens", str(cfg.get("deep_context_tokens")), project="QR", source="config"))
    labels = ", ".join(schedule_service.AGENT_LABELS)
    added.append(add_fact(
        "launchd_schedule_install",
        f"qr schedule install 安装的 launchd 任务: {labels}",
        project="QR",
        source="schedule_service",
    ))
    return added


# 综合评测报告 + 立项决策中的稳定事实（非易变指标）
_REPORT_FACTS: list[tuple[str, str, str | None, str]] = [
    (
        "retrieval_upgrade_policy",
        "未满足 docs/RETRIEVAL_UPGRADE_PLAN.md 触发条件前，不实施检索阶段 C～F 大工程",
        "QR",
        "决策/评测报告",
    ),
    (
        "ai_eval_rhythm",
        "每日 qr ai-assess --save；每月 qr eval monthly --save（检索基线 + 行为快照）",
        "QR",
        "评测报告",
    ),
    (
        "exam_region",
        "河北省中考体育",
        "dev/project-sports",
        "立项/补回",
    ),
    (
        "project_id",
        "dev/project-sports",
        "dev/project-sports",
        "立项/补回",
    ),
    (
        "deploy_scene",
        "400米跑道操场+篮球/乒乓球场地",
        "dev/project-sports",
        "立项/补回",
    ),
    (
        "mvp_focus",
        "立定跳远·单路视频端到端判分",
        "dev/project-sports",
        "立项/补回",
    ),
    (
        "mvp_version",
        "0.1.0",
        "dev/project-sports",
        "立项/补回",
    ),
    (
        "conda_env",
        "sports",
        "dev/project-sports",
        "立项/补回",
    ),
    (
        "project_id",
        "dev/scribe",
        "dev/scribe",
        "评测报告",
    ),
    (
        "cursor_workspace",
        "~/QR/dev/scribe",
        "dev/scribe",
        "评测报告",
    ),
    (
        "manuscript_boundary",
        "写作手稿在 works/ 与 ~/.scribe；章节正文不进 qr.db 业务表",
        "dev/scribe",
        "评测报告",
    ),
]


def restore_report_facts() -> list[dict]:
    """从 config 同步 QR 事实，并写入报告/立项中的跨项目稳定事实。"""
    added = sync_from_config()
    for key, value, project, source in _REPORT_FACTS:
        added.append(add_fact(key, value, project=project, source=source))
    return added
