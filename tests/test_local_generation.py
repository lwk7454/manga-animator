"""Local transport boundaries and required, resumable translations."""

import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from koma.clients import LLMError, OllamaClient, OpenAICompatClient, text_client
from koma.config import Settings
from koma.gpu.worker import ComfyUIWorker
from koma.models.domain import PanelState
from koma.network import require_local_endpoint
from koma.steps.script import script_chapter
from tests.test_phase2 import FakeClient, FakeOcr, _script_store


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


class TranslationClient(FakeClient):
    def __init__(self, failure=None):
        super().__init__()
        self.failure = failure
        self.translations = []

    def chat_json(self, model, messages, **kwargs):
        key = "en" if "English" in messages[-1]["content"] else "zh-HANS"
        self.translations.append(key)
        if key == "zh-HANS" and self.failure:
            if self.failure == "error":
                raise LLMError("temporarily unavailable")
            return self.failure
        return super().chat_json(model, messages, **kwargs)


@pytest.mark.parametrize(
    "failure",
    [
        "error",
        {"lines": []},
        {"lines": [{"zh-HANS": " "}]},
        {"lines": [{"zh-HANS": "ok"}, {}]},
        {"unexpected": True},
    ],
)
def test_missing_translation_blocks_and_retries_only_missing(tmp_path, failure):
    store, slug = _script_store(tmp_path)
    client = TranslationClient(failure)
    ocr = Mock(wraps=FakeOcr())
    first = script_chapter(store, slug, 1, client=client, ocr=ocr)
    assert first["failed"] == 6 and first["scripted"] == 0
    assert all(panel.state is PanelState.SCRIPT_FAILED for panel in store.all_panels(slug))
    assert store.load_chapters(slug)[0].state == "script_incomplete"
    entries = json.loads(Path(first["script"]).read_text())["panels"]
    assert all(entry["translations"]["en"] == "Good morning" for entry in entries.values())
    assert all(entry["complete"] is False for entry in entries.values())
    client.failure = None
    client.calls.clear()
    client.translations.clear()
    ocr.reset_mock()
    second = script_chapter(store, slug, 1, client=client, ocr=ocr)
    assert second["scripted"] == 6 and second["failed"] == 0
    assert client.translations == ["zh-HANS"] * 6
    assert all(prompt == "json" for _, prompt in client.calls)
    ocr.read_region.assert_not_called()
    assert store.load_chapters(slug)[0].state == "scripted"


def test_no_dialogue_requires_no_translations(tmp_path):
    store, slug = _script_store(tmp_path)
    client = TranslationClient("error")
    ocr = Mock()
    ocr.read_region.return_value = ""
    assert script_chapter(store, slug, 1, client=client, ocr=ocr)["failed"] == 0
    assert client.translations == []


def test_new_subtitle_language_reuses_other_steps(tmp_path):
    store, slug = _script_store(tmp_path)
    series = store.load_series(slug)
    series.meta.subtitle_languages = ["en"]
    store.save_series(series)
    client = TranslationClient()
    script_chapter(store, slug, 1, client=client, ocr=FakeOcr())
    series.meta.subtitle_languages.append("zh-HANS")
    store.save_series(series)
    client.calls.clear()
    client.translations.clear()
    ocr = Mock(wraps=FakeOcr())
    assert script_chapter(store, slug, 1, client=client, ocr=ocr)["scripted"] == 6
    assert client.translations == ["zh-HANS"] * 6
    assert all(prompt == "json" for _, prompt in client.calls)
    ocr.read_region.assert_not_called()


def test_stale_entry_cannot_revive_failed_script(tmp_path):
    store, slug = _script_store(tmp_path)
    pid = store.all_panels(slug)[0].panel_id
    path = store.chapter_dir(slug, 1) / "script.json"
    path.write_text(json.dumps({"panels": {pid: {"caption": "old", "complete": True}}}))
    result = script_chapter(
        store, slug, 1, client=FakeClient(fail_on="vision"), ocr=FakeOcr(), limit=1
    )
    assert result["failed"] == 1
    assert store.find_panel(slug, pid)[1].state is PanelState.SCRIPT_FAILED
    assert store.load_chapters(slug)[0].state == "script_incomplete"


def test_script_accepts_ordered_without_gate(tmp_path):
    store, slug = _script_store(tmp_path)
    pages = store.load_pages(slug)
    for panel in pages[0].panels:
        panel.state = PanelState.ORDERED
    store.save_pages(slug, pages)
    result = script_chapter(store, slug, 1, client=FakeClient(), ocr=FakeOcr(), limit=1)
    assert result["scripted"] == 1
    assert store.load_chapters(slug)[0].state == "script_incomplete"


def test_checkpoint_precedes_panel_advancement(tmp_path, monkeypatch):
    store, slug = _script_store(tmp_path)
    real_set = store.set_panel_state

    def interrupt(*args):
        raise RuntimeError("simulated interruption after checkpoint")

    monkeypatch.setattr(store, "set_panel_state", interrupt)
    with pytest.raises(RuntimeError, match="interruption"):
        script_chapter(store, slug, 1, client=FakeClient(), ocr=FakeOcr(), limit=1)
    monkeypatch.setattr(store, "set_panel_state", real_set)
    client = TranslationClient()
    ocr = Mock(wraps=FakeOcr())
    result = script_chapter(store, slug, 1, client=client, ocr=ocr, limit=1)
    assert result["scripted"] == 1
    assert client.calls == []
    ocr.read_region.assert_not_called()


def test_limited_source_revision_does_not_claim_old_entries_complete(tmp_path):
    store, slug = _script_store(tmp_path)
    script_chapter(store, slug, 1, client=FakeClient(), ocr=FakeOcr())
    page = store.load_pages(slug)[0]
    source = store.series_dir(slug) / "source" / page.source_path
    source.write_bytes(source.read_bytes() + b"new revision")
    result = script_chapter(store, slug, 1, client=FakeClient(), ocr=FakeOcr(), limit=1)
    assert result["scripted"] == 1
    assert store.load_chapters(slug)[0].state == "script_incomplete"


def test_restart_repairs_chapter_status_without_new_model_calls(tmp_path, monkeypatch):
    store, slug = _script_store(tmp_path)
    real_update = store.update_chapter
    monkeypatch.setattr(store, "update_chapter", Mock(side_effect=RuntimeError("interrupted")))
    with pytest.raises(RuntimeError, match="interrupted"):
        script_chapter(store, slug, 1, client=FakeClient(), ocr=FakeOcr())
    monkeypatch.setattr(store, "update_chapter", real_update)
    client = FakeClient()
    result = script_chapter(store, slug, 1, client=client, ocr=FakeOcr())
    assert result["scripted"] == 0 and client.calls == []
    assert store.load_chapters(slug)[0].state == "scripted"
