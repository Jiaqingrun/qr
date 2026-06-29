"""AI 服务开关：省电模式停 Ollama 并暂停屏幕采样，Web/采集其余能力保留。"""
from __future__ import annotations

from typing import Any

import httpx

from . import config, ollama_runtime, tracker

MODE_FULL = "full"
MODE_LITE = "lite"


def is_lite() -> bool:
    return str(config.load_config().get("power_mode", MODE_FULL)).strip().lower() == MODE_LITE


def is_ai_enabled() -> bool:
    return not is_lite()


def _base_url() -> str:
    return str(config.load_config().get("ollama_url", "http://localhost:11434")).rstrip("/")


def _ping_ollama(timeout: float = 2.0) -> bool:
    try:
        r = httpx.get(f"{_base_url()}/api/tags", timeout=timeout, trust_env=False)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def _loaded_models() -> list[str]:
    try:
        r = httpx.get(f"{_base_url()}/api/ps", timeout=10.0, trust_env=False)
        r.raise_for_status()
        return [
            str(m.get("name"))
            for m in (r.json().get("models") or [])
            if m.get("name")
        ]
    except httpx.HTTPError:
        return []


def stop_ollama_models() -> dict[str, Any]:
    """卸载已加载模型，并释放 QR 按需会话占用的 serve。"""
    errors: list[str] = []
    try:
        ollama_runtime.release_all()
    except Exception as exc:
        errors.append(str(exc))
    stopped = ollama_runtime.unload_models()
    return {"stopped": stopped, "errors": errors}


def _hint(*, lite: bool, ollama_reachable: bool, models_loaded: int) -> str:
    if lite:
        parts = ["关 · 已停 AI"]
        if models_loaded:
            parts.append(f"模型 {models_loaded}")
        parts.append("采集暂停")
        return " · ".join(parts)
    if ollama_reachable and models_loaded:
        return f"开 · 问答与检索可用 · 模型 {models_loaded}"
    if ollama_reachable:
        return "开 · 问答与检索可用"
    return "开 · 按需启动 Ollama"


def status() -> dict[str, Any]:
    lite = is_lite()
    reachable = _ping_ollama()
    models = _loaded_models() if reachable else []
    paused = tracker.is_tracking_paused()
    mode = MODE_LITE if lite else MODE_FULL
    return {
        "mode": mode,
        "ai_enabled": not lite,
        "ollama_reachable": reachable,
        "ollama_models_loaded": len(models),
        "ollama_models": models,
        "tracker_paused": paused,
        "hint": _hint(lite=lite, ollama_reachable=reachable, models_loaded=len(models)),
        "message": "AI 服务已关闭（省电）" if lite else "AI 服务已开启",
    }


def set_mode(mode: str) -> dict[str, Any]:
    target = MODE_LITE if str(mode).strip().lower() == MODE_LITE else MODE_FULL
    cfg = config.load_config()
    if target == MODE_LITE:
        stop_result = stop_ollama_models()
        cfg["power_mode"] = MODE_LITE
        config.save_config(cfg)
        out = status()
        out["ollama_stop"] = stop_result
        return out

    cfg["power_mode"] = MODE_FULL
    config.save_config(cfg)
    return status()


def toggle() -> dict[str, Any]:
    return set_mode(MODE_FULL if is_lite() else MODE_LITE)


def set_enabled(enabled: bool) -> dict[str, Any]:
    return set_mode(MODE_FULL if enabled else MODE_LITE)
