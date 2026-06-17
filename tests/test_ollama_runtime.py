from __future__ import annotations

from unittest.mock import patch

from qr import ollama_runtime


def test_on_demand_refcount(monkeypatch):
    monkeypatch.setattr(ollama_runtime, "on_demand_enabled", lambda: True)
    monkeypatch.setattr(ollama_runtime, "_start_serve", lambda: None)
    monkeypatch.setattr(ollama_runtime, "_unload_models", lambda: None)
    monkeypatch.setattr(ollama_runtime, "_stop_serve", lambda: None)
    ollama_runtime._refcount = 0

    ollama_runtime.session_begin()
    assert ollama_runtime._refcount == 1
    ollama_runtime.session_begin()
    assert ollama_runtime._refcount == 2
    ollama_runtime.session_end()
    assert ollama_runtime._refcount == 1
    ollama_runtime.session_end()
    assert ollama_runtime._refcount == 0


def test_needs_boot_when_offline(monkeypatch):
    monkeypatch.setattr(ollama_runtime, "on_demand_enabled", lambda: True)
    with patch.object(ollama_runtime, "_ping", return_value=False):
        assert ollama_runtime.needs_boot() is True
    with patch.object(ollama_runtime, "_ping", return_value=True):
        assert ollama_runtime.needs_boot() is False
