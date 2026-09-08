"""Clients — LLM / OCR / TTS / remote-run adapters (decisions #014/#015/#018).

Secrets from env only (KOMA_*). No client hardcodes credentials or endpoints
beyond public defaults. Phase 2 ships: OllamaClient (text+vision), the
OpenAI-compatible fallback, and MangaOcrClient. TTSClient and SSHRunner land
with the GPU phases.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import httpx


class LLMError(RuntimeError):
    """LLM call failed after retries — callers decide fail-open vs fail-closed."""


def _b64_image(path: Path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode()


def _post_with_retry(
    client: httpx.Client,
    path: str,
    payload: dict[str, Any],
    timeout: float,
    attempts: int = 5,
):
    """POST JSON with bounded retry on transient transport errors AND
    transient HTTP statuses (429 rate-limit, 5xx server errors).

    httpx exceptions (ReadTimeout etc.) must NEVER escape raw: callers catch
    LLMError to fail a single panel — an escaped httpx error kills the whole
    run (learned the hard way Sep 2026: one ReadTimeout aborted a 240-panel
    run after all vision work was done). Cloud rate-limiting under the script
    stage's concurrency-8 pool shows up as failure-count creep (8→21 over one
    243-panel run) unless 429/5xx are retried too.
    """
    import time

    last: Exception | str | None = None
    for attempt in range(attempts):
        try:
            resp = client.post(path, json=payload, timeout=timeout)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last = e
        else:
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                last = f"http {resp.status_code}"
                # honor Retry-After when the server sends one
                delay = float(resp.headers.get("retry-after", 0) or 0)
                time.sleep(min(max(delay, 2 ** attempt * 2), 20))
                continue
            return resp  # non-transient status: caller reports it
        if attempt < attempts - 1:
            time.sleep(min(2 ** attempt * 2, 8))
    raise LLMError(f"request failed after {attempts} attempts: {last}") from (
        last if isinstance(last, Exception) else None
    )


class OllamaClient:
    """Ollama chat client — local daemon or Ollama Cloud (same API shape).

    Routing rule (decision #018, two lanes): image-bearing calls go to the
    VISION endpoint (KOMA_VISION_BASE_URL, default http://127.0.0.1:11434,
    no auth — LAN-local: the 3090 box when serving llama.cpp); text calls go
    to cloud (KOMA_TEXT_BASE_URL, default https://ollama.com, Bearer key).
    No cloud provider ever sees explicit images. When the vision lane runs
    llama.cpp (OpenAI shape), the script step uses OpenAICompatClient instead.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
    ):
        cloud = "https://ollama.com"
        local = os.environ.get("KOMA_VISION_BASE_URL", "http://127.0.0.1:11434")
        # Explicit base_url = honour verbatim (tests/local overrides).
        self._base = base_url if base_url is not None else os.environ.get(
            "KOMA_TEXT_BASE_URL", cloud
        )
        api_key = api_key or os.environ.get("KOMA_TEXT_API_KEY") or os.environ.get("OLLAMA_API_KEY")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(base_url=self._base, headers=headers, timeout=timeout)
        self._timeout = timeout
        # second client for the OTHER lane; same object if endpoints coincide
        other = local if self._base != local else os.environ.get("KOMA_TEXT_BASE_URL", cloud)
        self._other = (
            httpx.Client(base_url=other, timeout=timeout)
            if other != self._base
            else self._client
        )
        self._other_base = other
        self._is_openai = False

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        images: list[Path] | None = None,
        temperature: float = 0.4,
        json_mode: bool = False,
        num_predict: int | None = None,
    ) -> str:
        """One chat completion. `images` attach to the LAST user message."""
        if images:
            last_user = next((m for m in reversed(messages) if m["role"] == "user"), None)
            if last_user is None:
                raise LLMError("images given but no user message to attach to")
            last_user = dict(last_user)
            last_user["images"] = [_b64_image(p) for p in images]
            messages = [*messages[:-1], last_user]
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": "30m",
            "options": {
                "temperature": temperature,
                # reasoning models emit a "thinking" field before "content";
                # a tight num_predict starves the content leg → empty reply.
                # json_mode calls (structured consolidation) get the headroom.
                "num_predict": num_predict or (3000 if json_mode else 900),
            },
        }
        if json_mode:
            payload["format"] = "json"
        # lane routing: image-bearing calls (vision) go LOCAL; text calls go
        # to this client's own base. When __init__ got an explicit base_url
        # (tests), _other IS _client so behaviour is unchanged.
        target = self._other if images else self._client
        resp = _post_with_retry(target, "/api/chat", payload, self._timeout)
        if resp.status_code != 200:
            raise LLMError(f"ollama http {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        content = data.get("message", {}).get("content", "")
        if not content:
            raise LLMError(f"empty completion: {str(data)[:300]}")
        return content

    def chat_json(self, model: str, messages: list[dict[str, Any]], **kw: Any) -> Any:
        """Chat with JSON-mode on; parses the reply (raises LLMError on garbage)."""
        import json
        import re

        text = self.chat(model, messages, json_mode=True, **kw)
        # tolerate fenced / leading-prose variants
        match = re.search(r"\{.*\}|\[.*\]", text, re.DOTALL)
        if not match:
            raise LLMError(f"no JSON object in reply: {text[:200]}")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as e:
            raise LLMError(f"bad JSON in reply: {e}: {text[:200]}") from e

    def close(self) -> None:
        self._client.close()


class OpenAICompatClient(OllamaClient):
    """OpenAI-compatible endpoint (OpenRouter etc.) — same surface, /chat/completions.

    Used as the text-LLM fallback lane (decision #014: swappable adapters).
    """

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        images: list[Path] | None = None,
        **kw: Any,
    ) -> str:
        if images:
            last_user = next((m for m in reversed(messages) if m["role"] == "user"), None)
            if last_user is None:
                raise LLMError("images given but no user message")
            text_part: dict[str, Any] = {"type": "text", "text": last_user.get("content", "")}
            img_parts = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{_b64_image(p)}"},
                }
                for p in images
            ]
            parts = [text_part, *img_parts]
            last_user = {"role": "user", "content": parts}
            messages = [*messages[:-1], last_user]
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": kw.get("temperature", 0.4),
        }
        if kw.get("max_tokens"):
            body["max_tokens"] = kw["max_tokens"]
        resp = _post_with_retry(self._client, "/v1/chat/completions", body, self._timeout)
        if resp.status_code != 200:
            raise LLMError(f"openai-compat http {resp.status_code}: {resp.text[:300]}")
        return resp.json()["choices"][0]["message"]["content"]


class MangaOcrClient:
    """manga-ocr (kha-white/manga-ocr-base) — JP text from panel crops, Mac CPU.

    Lazy-loads on first call (~450MB, cached in ~/.cache). One instance for
    the whole run; thread-hostile, call from the ingest loop only.
    """

    def __init__(self) -> None:
        self._ocr = None

    def _ensure(self) -> None:
        if self._ocr is None:
            from manga_ocr import MangaOcr

            self._ocr = MangaOcr()

    def read(self, image_path: Path) -> str:
        self._ensure()
        return str(self._ocr(str(image_path)))

    def read_region(self, page_path: Path, bbox: tuple[int, int, int, int]) -> str:
        """Crop bbox (x, y, w, h) from page then OCR — avoids full-page noise."""
        import cv2

        img = cv2.imread(str(page_path))
        if img is None:
            return ""
        x, y, w, h = bbox
        crop = img[y : y + h, x : x + w]
        ok, buf = cv2.imencode(".png", crop)
        if not ok:
            return ""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(buf.tobytes())
            tmp = Path(f.name)
        try:
            return self.read(tmp)
        finally:
            tmp.unlink(missing_ok=True)


__all__ = ["LLMError", "MangaOcrClient", "OllamaClient", "OpenAICompatClient"]
