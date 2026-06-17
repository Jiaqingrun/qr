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


def _ollama_bin() -> str:
    cfg = config.load_config()
    path = (cfg.get("ollama_bin") or "").strip() or shutil.which("ollama")
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


def _unload_models() -> None:
    try:
        r = httpx.get(f"{_base_url()}/api/ps", timeout=10.0, trust_env=False)
        r.raise_for_status()
        for m in r.json().get("models") or []:
            name = m.get("name")
            if name:
                subprocess.run(
                    [_ollama_bin(), "stop", name],
                    capture_output=True,
                    timeout=30,
                )
    except (httpx.HTTPError, subprocess.TimeoutExpired, OSError):
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


class ollama_session:
    def __enter__(self) -> ollama_session:
        session_begin()
        return self

    def __exit__(self, *_exc: object) -> None:
        session_end()
