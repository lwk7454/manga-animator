"""Durable feature tests; no live model services."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


class TestClients:
    def test_ollama_client_image_attach(self, tmp_path: Path) -> None:
        import httpx as _httpx

        from koma.clients import OllamaClient

        img = tmp_path / "i.png"
        img.write_bytes(b"\x89PNG fake")

        captured: dict[str, Any] = {}

        def handler(request: _httpx.Request) -> _httpx.Response:
            captured["body"] = json.loads(request.content)
            return _httpx.Response(200, json={"message": {"content": "hi"}})

        client = OllamaClient(base_url="http://127.0.0.1:11434")
        client._client = _httpx.Client(
            base_url="http://127.0.0.1:11434", transport=_httpx.MockTransport(handler)
        )
        out = client.chat(
            "m", [{"role": "user", "content": "describe"}], images=[img]
        )
        assert out == "hi"
        sent = captured["body"]["messages"][0]
        assert sent["images"]  # base64 attached
        assert isinstance(sent["images"][0], str)

    def test_chat_json_parses_fenced(self) -> None:
        import httpx as _httpx

        from koma.clients import OllamaClient

        def handler(request: _httpx.Request) -> _httpx.Response:
            content = 'Sure! {"lines": [{"jp": "x"}]}'
            return _httpx.Response(200, json={"message": {"content": content}})

        client = OllamaClient(base_url="http://127.0.0.1:11434")
        client._client = _httpx.Client(
            base_url="http://127.0.0.1:11434", transport=_httpx.MockTransport(handler)
        )
        data = client.chat_json("m", [{"role": "user", "content": "q"}])
        assert data == {"lines": [{"jp": "x"}]}

    def test_chat_json_garbage_raises(self) -> None:
        import httpx as _httpx

        from koma.clients import LLMError, OllamaClient

        def handler(request: _httpx.Request) -> _httpx.Response:
            return _httpx.Response(200, json={"message": {"content": "no json here"}})

        client = OllamaClient(base_url="http://127.0.0.1:11434")
        client._client = _httpx.Client(
            base_url="http://127.0.0.1:11434", transport=_httpx.MockTransport(handler)
        )
        with pytest.raises(LLMError):
            client.chat_json("m", [{"role": "user", "content": "q"}])

    def test_cloud_api_key_is_not_inherited(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx as _httpx

        from koma.clients import OllamaClient

        monkeypatch.setenv("KOMA_TEXT_API_KEY", "sk-test")
        captured: dict[str, Any] = {}

        def handler(request: _httpx.Request) -> _httpx.Response:
            captured["auth"] = request.headers.get("authorization")
            return _httpx.Response(200, json={"message": {"content": "ok"}})

        # Preserve the actual constructor's headers while replacing all transport
        # routing, including proxy mounts inherited from the environment.
        client = OllamaClient(base_url="http://127.0.0.1:11434")
        original = client._client
        client._client = _httpx.Client(
            base_url=original.base_url, headers=original.headers,
            transport=_httpx.MockTransport(handler),
        )
        original.close()
        client.chat("m", [{"role": "user", "content": "q"}])
        assert captured["auth"] is None
