from __future__ import annotations

from . import config

# 问答可选模型（与 config.ask_models 合并，以 config 为准）
_DEFAULT_ASK_MODELS: list[dict] = [
    {
        "id": "qwen2.5:32b",
        "label": "Qwen 2.5 · 32B",
        "hint": "默认推荐，日常查阅与总结",
        "reasoning": False,
        "default": True,
    },
    {
        "id": "deepseek-r1:32b",
        "label": "DeepSeek R1 · 32B",
        "hint": "深度推理，复杂架构与因果分析",
        "reasoning": True,
    },
]


def ask_catalog(cfg: dict | None = None) -> list[dict]:
    cfg = cfg or config.load_config()
    raw = cfg.get("ask_models")
    if isinstance(raw, list) and raw:
        out = []
        for item in raw:
            if isinstance(item, dict) and item.get("id"):
                out.append(dict(item))
        if out:
            return out
    return [dict(m) for m in _DEFAULT_ASK_MODELS]


def _by_id(cfg: dict | None = None) -> dict[str, dict]:
    return {m["id"]: m for m in ask_catalog(cfg)}


def default_ask_model(cfg: dict | None = None) -> str:
    cfg = cfg or config.load_config()
    explicit = (cfg.get("default_ask_model") or "").strip()
    if explicit and explicit in _by_id(cfg):
        return explicit
    for m in ask_catalog(cfg):
        if m.get("default"):
            return m["id"]
    return ask_catalog(cfg)[0]["id"]


def default_reasoning_model(cfg: dict | None = None) -> str:
    cfg = cfg or config.load_config()
    legacy = (cfg.get("deep_model") or "").strip()
    if legacy and legacy in _by_id(cfg):
        return legacy
    for m in ask_catalog(cfg):
        if m.get("reasoning"):
            return m["id"]
    return default_ask_model(cfg)


def is_valid_ask_model(model: str, cfg: dict | None = None) -> bool:
    return model in _by_id(cfg)


def is_reasoning_model(model: str, cfg: dict | None = None) -> bool:
    meta = _by_id(cfg).get(model)
    if meta is not None:
        return bool(meta.get("reasoning"))
    return "deepseek-r1" in (model or "").lower()


def resolve_ask_model(
    model: str | None = None,
    *,
    deep_legacy: bool = False,
    session_model: str | None = None,
    cfg: dict | None = None,
) -> str:
    """解析本次问答使用的 Ollama 模型名（必须在 ask_models 目录内）。"""
    cfg = cfg or config.load_config()
    catalog = _by_id(cfg)
    if model and model.strip():
        mid = model.strip()
        if mid not in catalog:
            raise ValueError(
                f"未知模型 {mid!r}，可选: {', '.join(catalog.keys())}"
            )
        return mid
    if session_model and session_model in catalog:
        return session_model
    if deep_legacy:
        return default_reasoning_model(cfg)
    return default_ask_model(cfg)


def model_label(model: str, cfg: dict | None = None) -> str:
    meta = _by_id(cfg).get(model)
    return meta["label"] if meta else model


def list_ask_models_with_status(installed: list[str] | None = None) -> list[dict]:
    """返回目录 + 是否已在 ollama 拉取。"""
    installed_full = set(installed or [])
    out = []
    for m in ask_catalog():
        mid = m["id"]
        ok = mid in installed_full
        if not ok:
            for name in installed_full:
                if name == mid or name.startswith(f"{mid}:") or mid in name:
                    ok = True
                    break
        row = dict(m)
        row["installed"] = ok
        out.append(row)
    return out
