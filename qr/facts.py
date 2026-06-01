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
    cfg = config.load_config()
    added = []
    added.append(add_fact("embed_model", str(cfg.get("embed_model")), project="QR", source="config"))
    added.append(add_fact("chat_model", str(cfg.get("chat_model")), project="QR", source="config"))
    added.append(add_fact("web_port", str(cfg.get("web_port", 8765)), project="QR", source="config"))
    added.append(add_fact("context_tokens", str(cfg.get("context_tokens")), project="QR", source="config"))
    added.append(add_fact("deep_context_tokens", str(cfg.get("deep_context_tokens")), project="QR", source="config"))
    return added
