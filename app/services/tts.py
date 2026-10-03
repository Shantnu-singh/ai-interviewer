"""Sarvam text-to-speech: a thin synchronous REST client.

Request/response format: https://docs.sarvam.ai/api-reference-docs/text-to-speech/api/rest-api
The REST endpoint returns JSON with a base64-encoded WAV (or other codec) in `audios[0]`.
"""

from __future__ import annotations

import base64
import logging

import httpx

from app.config import Settings

log = logging.getLogger(__name__)

MAX_TTS_CHARS = 1500  # bulbul:v2 limit; keep interviewer replies under this


class TtsError(Exception):
    pass


def synthesize(settings: Settings, text: str) -> bytes:
    """Convert text to speech and return raw audio bytes (WAV by default)."""
    if not settings.sarvam_api_key:
        raise TtsError("SARVAM_API_KEY is not configured")
    text = text.strip()[:MAX_TTS_CHARS]
    if not text:
        raise TtsError("No text to synthesize")

    body = {
        "text": text,
        "speaker": settings.sarvam_speaker,
        "model": settings.sarvam_model,
        "language_code": settings.sarvam_language_code,
        "pitch": 0,
        "pace": 1,
        "loudness": 1,
        "speech_sample_rate": 22050,
        "enable_preprocessing": True,
    }
    try:
        resp = httpx.post(
            f"{settings.sarvam_base_url}/text-to-speech",
            headers={
                "API-Subscription-Key": settings.sarvam_api_key,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise TtsError(f"Could not reach Sarvam: {type(exc).__name__}") from exc

    if resp.status_code >= 400:
        log.warning("sarvam tts failed: %s %s", resp.status_code, resp.text[:300])
        raise TtsError(f"Sarvam returned HTTP {resp.status_code}")

    try:
        data = resp.json()
        audios = data["audios"]
        if not audios:
            raise TtsError("Sarvam returned no audio")
        return base64.b64decode(audios[0])
    except (KeyError, ValueError, TypeError) as exc:
        raise TtsError(f"Could not parse Sarvam response: {type(exc).__name__}") from exc
