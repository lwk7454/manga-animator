"""Durable feature tests; no live model services."""

from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest

from koma.clients import LLMError, OllamaClient, OpenAICompatClient, text_client
from koma.config import Settings
from koma.gpu.worker import ComfyUIWorker
from koma.network import require_local_endpoint


@pytest.mark.parametrize(
    "url",
    [
        "https://ollama.com",
        "https://api.openai.com",
        "http://8.8.8.8",
        "http://example.local",
        "http://169.254.169.254",
        "http://127.0.0.1@evil.com",
        "ftp://127.0.0.1",
    ],
)
def test_public_endpoints_rejected(url):
    with pytest.raises(ValueError):
        require_local_endpoint(url)
    for adapter in (OllamaClient, OpenAICompatClient):
        with pytest.raises(LLMError):
            adapter(base_url=url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:8000",
        "http://192.168.50.103:11434",
        "http://10.0.0.2",
        "http://172.16.0.2",
        "http://[::1]:8000",
        "http://[fd00::1]",
    ],
)
def test_local_endpoints_allowed(url):
    assert require_local_endpoint(url) == url


def test_cloud_text_env_rejected(monkeypatch):
    monkeypatch.setenv("KOMA_TEXT_BASE_URL", "https://ollama.com")
    with pytest.raises(LLMError):
        text_client(Settings())


def test_text_defaults_share_local_vision_server():
    cfg = Settings()
    assert cfg.text_base_url == cfg.vision_base_url
    assert cfg.text_llm == cfg.vision_model
    client = text_client(cfg)
    assert isinstance(client, OpenAICompatClient)
    client.close()


@pytest.mark.parametrize("adapter", [OllamaClient, OpenAICompatClient])
def test_local_transport_no_proxy_redirect_or_cloud_credentials(monkeypatch, tmp_path, adapter):
    monkeypatch.setenv("KOMA_TEXT_API_KEY", "cloud-key")
    monkeypatch.setenv("OLLAMA_API_KEY", "cloud-key")
    monkeypatch.setenv("HTTPS_PROXY", "http://public-proxy.invalid:8000")
    original_client = httpx.Client
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://cloud.invalid/upload"})

    def client_factory(*args, **kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original_client(*args, **kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", client_factory)
    client = adapter(base_url="http://127.0.0.1:11434")
    image = tmp_path / "image.png"
    image.write_bytes(b"synthetic")
    with pytest.raises(LLMError, match="302"):
        client.chat("model", [{"role": "user", "content": "Describe this."}], images=[image])
    assert len(calls) == 1
    assert calls[0].url.host == "127.0.0.1"
    assert "authorization" not in calls[0].headers
    client.close()


def test_gpu_rejects_public_host():
    with pytest.raises(ValueError):
        ComfyUIWorker("8.8.8.8")


def test_gpu_requests_disable_proxies_and_redirects(monkeypatch):
    get = Mock(return_value=httpx.Response(200, json={}))
    monkeypatch.setattr(httpx, "get", get)
    assert ComfyUIWorker("127.0.0.1").is_online()
    assert get.call_args.kwargs["trust_env"] is False
    assert get.call_args.kwargs["follow_redirects"] is False
