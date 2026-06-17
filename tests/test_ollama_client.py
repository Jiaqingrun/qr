from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx

from qr.ollama_client import Ollama, _embed_error_message, _embed_text_variants, _is_retriable_embed_error


def test_retriable_embed_error_detects_nan():
    assert _is_retriable_embed_error("json: unsupported value: NaN")
    assert not _is_retriable_embed_error("connection refused")


def test_embed_error_message_includes_flash_attention_hint():
    msg = _embed_error_message("json: unsupported value: NaN")
    assert "OLLAMA_FLASH_ATTENTION" in msg


def test_embed_text_variants_include_truncation():
    text = "x" * 5000
    variants = _embed_text_variants(text)
    assert text in variants
    assert any(len(v) == 1200 for v in variants)


def test_embed_retries_without_dimensions_on_nan():
    ol = Ollama()
    ol.embed_dim = 4096
    ol.embed_model = "qwen3-embedding:8b"
    ol.url = "http://localhost:11434"

    fail = httpx.Response(
        500,
        request=httpx.Request("POST", "http://localhost:11434/api/embed"),
        content=json.dumps({"error": "json: unsupported value: NaN"}).encode(),
    )
    ok_body = {"embeddings": [[0.1] * 4096]}
    ok = httpx.Response(
        200,
        request=httpx.Request("POST", "http://localhost:11434/api/embed"),
        content=json.dumps(ok_body).encode(),
    )

    client = MagicMock()
    client.post.side_effect = [fail, ok]
    ol._client = client

    emb = ol.embed("测试")
    assert emb == [0.1] * 4096
    assert client.post.call_count == 2
    second_payload = client.post.call_args_list[1].kwargs["json"]
    assert "dimensions" not in second_payload
