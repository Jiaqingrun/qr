"""Ollama 按需启停：提问前拉起，结束后释放 GPU / 停止由 QR 启动的 serve。"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time

import httpx

from . import config
from .ollama_client import OllamaError

_lock = threading.Lock()
_refcount = 0
_proc: subprocess.Popen | None = None
_started_by_qr = False


def on_demand_enabled() -> bool:
    return bool(config.load_config().get("ollama_on_demand", False))


def needs_boot() -> bool:
    return on_demand_enabled() and not _ping()


def _ollama_bin_optional() -> str | None:
    cfg = config.load_config()
    path = (cfg.get("ollama_bin") or "").strip()
    if path:
        return path
    found = shutil.which("ollama")
    if found:
        return found
    for candidate in (
        "/usr/local/bin/ollama",
        "/opt/homebrew/bin/ollama",
        os.path.expanduser("~/Applications/Ollama.app/Contents/Resources/ollama"),
        "/Applications/Ollama.app/Contents/Resources/ollama",
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def _ollama_bin() -> str:
    path = _ollama_bin_optional()
    if not path:
        raise OllamaError("未找到 ollama 命令，请先安装 Ollama")
    return path


def _base_url() -> str:
    return str(config.load_config().get("ollama_url", "http://localhost:11434")).rstrip("/")


def _ping(timeout: float = 2.0) -> bool:
    try:
        r = httpx.get(f"{_base_url()}/api/tags", timeout=timeout, trust_env=False)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def _serve_env() -> dict[str, str]:
    env = os.environ.copy()
    cfg = config.load_config()
    flash = cfg.get("ollama_flash_attention")
    if flash is not None:
        env["OLLAMA_FLASH_ATTENTION"] = "true" if flash else "false"
    elif "OLLAMA_FLASH_ATTENTION" not in env:
        env["OLLAMA_FLASH_ATTENTION"] = "false"
    kv = cfg.get("ollama_kv_cache_type")
    if kv:
        env["OLLAMA_KV_CACHE_TYPE"] = str(kv)
    return env


def _wait_ready(timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _ping():
            return
        if _proc is not None and _proc.poll() is not None:
            raise OllamaError("Ollama 进程启动后异常退出")
        time.sleep(0.5)
    raise OllamaError(
        f"Ollama 启动超时（{int(timeout)} 秒）。请确认已安装嵌入/对话模型，或手动执行 ollama serve"
    )


def _start_serve() -> None:
    global _proc, _started_by_qr
    if _ping():
        _started_by_qr = False
        return
    cfg = config.load_config()
    _proc = subprocess.Popen(
        [_ollama_bin(), "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_serve_env(),
        start_new_session=True,
    )
    _started_by_qr = True
    boot_timeout = float(cfg.get("ollama_boot_timeout_seconds", 90))
    _wait_ready(boot_timeout)


def _unload_model_by_name(name: str) -> None:
    try:
        httpx.post(
            f"{_base_url()}/api/generate",
            json={"model": name, "prompt": "", "keep_alive": 0},
            timeout=30.0,
            trust_env=False,
        )
    except httpx.HTTPError:
        pass
    binary = _ollama_bin_optional()
    if not binary:
        return
    try:
        subprocess.run(
            [binary, "stop", name],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _unload_models() -> None:
    try:
        r = httpx.get(f"{_base_url()}/api/ps", timeout=10.0, trust_env=False)
        r.raise_for_status()
        for m in r.json().get("models") or []:
            name = m.get("name")
            if name:
                _unload_model_by_name(name)
    except httpx.HTTPError:
        pass


def _stop_serve() -> None:
    global _proc, _started_by_qr
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            _proc.kill()
            _proc.wait(timeout=5)
    _proc = None
    _started_by_qr = False


def session_begin() -> None:
    global _refcount
    if not on_demand_enabled():
        return
    with _lock:
        _refcount += 1
        if _refcount == 1:
            _start_serve()


def session_end() -> None:
    global _refcount
    if not on_demand_enabled():
        return
    with _lock:
        if _refcount <= 0:
            return
        _refcount -= 1
        if _refcount > 0:
            return
        _unload_models()
        if _started_by_qr:
            _stop_serve()


def unload_models() -> list[str]:
    """卸载当前已加载模型；不依赖 PATH 中的 ollama CLI。"""
    names = []
    try:
        r = httpx.get(f"{_base_url()}/api/ps", timeout=10.0, trust_env=False)
        r.raise_for_status()
        names = [str(m.get("name")) for m in (r.json().get("models") or []) if m.get("name")]
    except httpx.HTTPError:
        return []
    for name in names:
        _unload_model_by_name(name)
    return names


def release_all() -> None:
    """强制释放 QR 持有的 Ollama 会话（unload 模型并停止 QR 拉起的 serve）。"""
    global _refcount
    with _lock:
        _refcount = 0
        _unload_models()
        _stop_serve()


class ollama_session:
    def __enter__(self) -> ollama_session:
        session_begin()
        return self

    def __exit__(self, *_exc: object) -> None:
        session_end()
