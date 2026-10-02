"""Deepgram streaming speech-to-text: a thin async WebSocket client.

Audio in is expected to be linear16 (16-bit signed PCM, little-endian), mono, 16kHz —
the browser resamples/encodes to this format before sending. See the Deepgram streaming
reference: https://developers.deepgram.com/reference/speech-to-text/listen-streaming
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from websockets.asyncio.client import ClientConnection, connect

from app.config import Settings

log = logging.getLogger(__name__)


class SttError(Exception):
    pass


@dataclass(frozen=True)
class TranscriptChunk:
    text: str
    is_final: bool
    speech_final: bool


def parse_message(raw: str | bytes) -> TranscriptChunk | None:
    """Pure parser for a single Deepgram streaming message (unit tested)."""
    try:
        event = json.loads(raw)
    except ValueError:
        return None
    if event.get("type") != "Results":
        return None
    alternatives = (event.get("channel") or {}).get("alternatives") or []
    text = (alternatives[0].get("transcript") if alternatives else "") or ""
    if not text.strip():
        return None
    return TranscriptChunk(
        text=text.strip(),
        is_final=bool(event.get("is_final")),
        speech_final=bool(event.get("speech_final")),
    )


def _ws_url(settings: Settings) -> str:
    params = {
        "model": settings.deepgram_model,
        "language": "en",
        "encoding": "linear16",
        "sample_rate": "16000",
        "channels": "1",
        "punctuate": "true",
        "smart_format": "true",
        "interim_results": "true",
        "endpointing": "300",
        "vad_events": "true",
    }
    return f"{settings.deepgram_ws_url}?{urlencode(params)}"


async def connect_stream(settings: Settings) -> ClientConnection:
    """Open a Deepgram streaming connection. Caller is responsible for closing it."""
    if not settings.deepgram_api_key:
        raise SttError("DEEPGRAM_API_KEY is not configured")
    try:
        return await connect(
            _ws_url(settings),
            additional_headers={"Authorization": f"Token {settings.deepgram_api_key}"},
            open_timeout=15,
            max_size=4 * 1024 * 1024,
        )
    except OSError as exc:
        raise SttError(f"Could not reach Deepgram: {type(exc).__name__}") from exc


async def iter_transcripts(ws: ClientConnection) -> AsyncIterator[TranscriptChunk]:
    """Yield parsed transcript chunks from an open Deepgram connection."""
    async for raw in ws:
        chunk = parse_message(raw)
        if chunk is not None:
            yield chunk


async def close_stream(ws: ClientConnection) -> None:
    """Flush and close a Deepgram stream per their recommended handshake."""
    try:
        await ws.send(json.dumps({"type": "CloseStream"}))
    except Exception as exc:  # connection may already be closing  # noqa: BLE001
        log.debug("deepgram close send failed: %s", exc)
    finally:
        await ws.close()


def session_config_summary(settings: Settings) -> dict[str, Any]:
    """Non-secret debug summary of the configured streaming params."""
    return {"model": settings.deepgram_model, "encoding": "linear16", "sample_rate": 16000}
