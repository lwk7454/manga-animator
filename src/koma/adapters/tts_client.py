"""GPT-SoVITS inference-API client (first TTS backend, PLAN §17.1).

Wraps the REAL installed local API contract (api_v2.py on the 3090 box):
GET/POST with JSON, returns WAV bytes. Measured durations drive the
timeline (audio-first doctrine). No cloud, no key.

Setup state (Sep 16): server not yet installed/qualified — the client is
written against api_v2's documented contract and hardware qualification
(§24 step 4) activates the lane.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .tts import MeasuredAudio, TTSError

DEFAULT_TTS_HOST = "192.168.50.103"
DEFAULT_TTS_PORT = 9880  # api_v2 default


class GPTSoVITSClient:
    """Local GPT-SoVITS api_v2 client (zero-shot / few-shot voice clone)."""

    def __init__(
        self,
        host: str = DEFAULT_TTS_HOST,
        port: int = DEFAULT_TTS_PORT,
        timeout: float = 300.0,
    ) -> None:
        import httpx

        self._http = httpx.Client(base_url=f"http://{host}:{port}", timeout=timeout)

    def health(self) -> bool:
        try:
            r = self._http.get("/control", params={"action": "renew"}, timeout=5.0)
            return r.status_code == 200
        except Exception:
            return False

    def synthesize(
        self,
        text: str,
        *,
        text_lang: str = "ja",
        ref_audio_path: str,
        prompt_text: str,
        prompt_lang: str = "ja",
        speed_factor: float = 1.0,
        seed: int = -1,
    ) -> tuple[bytes, MeasuredAudio]:
        """Zero-shot synthesis: clone the voice from ref_audio_path.

        api_v2 contract: GET/POST /tts with
          text, text_lang, ref_audio_path (SERVER-SIDE path),
          aux_ref_audio_paths, prompt_text, prompt_lang, top_k, top_p,
          temperature, text_split_method, speed_factor, seed.
        Returns WAV bytes + measured duration. Errors raise TTSError.
        """
        if not text.strip():
            raise TTSError("empty synthesis text")
        payload: dict[str, Any] = {
            "text": text,
            "text_lang": text_lang,
            "ref_audio_path": ref_audio_path,
            "prompt_text": prompt_text,
            "prompt_lang": prompt_lang,
            "speed_factor": speed_factor,
            "seed": seed,
            "media_type": "wav",
            "streaming_mode": False,
        }
        r = self._http.post("/tts", json=payload)
        if r.status_code != 200:
            raise TTSError(f"tts api error {r.status_code}: {r.text[:300]}")
        data = r.content
        if not data.startswith(b"RIFF"):
            raise TTSError(f"tts returned non-WAV payload: {data[:60]!r}")
        return normalize_to_mix_pcm_from_bytes(data)


def normalize_to_mix_pcm_compat(wav_bytes: bytes) -> tuple[bytes, MeasuredAudio]:
    """Bridge to the store-layer PCM normalization (48k stereo s16)."""
    return normalize_to_mix_pcm_from_bytes(wav_bytes)


def normalize_to_mix_pcm_from_bytes(
    wav_bytes: bytes,
) -> tuple[bytes, MeasuredAudio]:
    """ffmpeg-normalize any WAV to 48kHz stereo PCM16 and measure it."""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src.wav"
        dst = Path(td) / "out.wav"
        src.write_bytes(wav_bytes)
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-ar", "48000", "-ac", "2",
             "-c:a", "pcm_s16le", str(dst)],
            capture_output=True, timeout=120,
        )
        if proc.returncode != 0:
            raise TTSError(f"ffmpeg normalize failed: {proc.stderr[-200:]!r}")
        data = dst.read_bytes()
    if len(data) < 44 or data[:4] != b"RIFF":
        raise TTSError("normalized payload is not RIFF/WAVE")
    samples = (len(data) - 44) // 4
    m = MeasuredAudio(sample_rate=48000, channels=2, samples=samples)
    m.validate()
    return data, m


# alias for the module-level helper name used by callers
normalize_to_mix_pcm = normalize_to_mix_pcm_from_bytes


def probe_tts_api(base_url: str) -> dict[str, Any]:
    """Honest capability probe for koma doctor (§24): reachable + contract."""
    import httpx

    try:
        r = httpx.get(f"{base_url}/control", params={"action": "renew"}, timeout=5)
        reachable = r.status_code in (200, 400)
    except Exception:
        reachable = False
    return {"reachable": reachable, "contract": "gpt-sovits api_v2"}


__all__ = [
    "DEFAULT_TTS_HOST",
    "DEFAULT_TTS_PORT",
    "GPTSoVITSClient",
    "normalize_to_mix_pcm",
    "normalize_to_mix_pcm_from_bytes",
    "probe_tts_api",
]
