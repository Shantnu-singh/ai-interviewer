"""Voice session worker: one WebSocket per interview call.

Replaces the old sideband worker (which rode shotgun on a second connection to an OpenAI
Realtime call). Now the browser connects *directly* to this process over a plain WebSocket
and this worker is the whole voice pipeline:

  browser audio (PCM16/16kHz) --> Deepgram STT --> OpenAI chat completion --> Sarvam TTS
      --> synthesized audio back to the browser

It records both sides of the transcript, enforces the time limit, and hands finished
interviews to the evaluation job -- same responsibilities as the old sideband worker.

Run with: `python -m worker.voice_session`
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
import uuid
from dataclasses import dataclass
from datetime import UTC, timedelta

import redis.asyncio as aioredis
from sqlalchemy import select
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from app.models import Interview, InterviewStatus, Message, Role, transition, utcnow
from app.services import interviewer, stt, tts
from app.services.stt import SttError
from app.services.tasks import end_key
from worker.context import WorkerContext, get_context

log = logging.getLogger("voice_session")

WRAP_UP_LEAD_SECONDS = 45  # ask the model to wrap up this long before the hard limit
ORPHAN_CLAIM_SECONDS = 120  # in_progress without a voice session for this long = failed setup
WATCHDOG_INTERVAL_SECONDS = 2


@dataclass(frozen=True)
class ControlMessage:
    type: str


def parse_control_message(raw: str | bytes) -> ControlMessage | None:
    """Parse a JSON control frame from the browser (pure; unit tested)."""
    if isinstance(raw, bytes):
        return None  # binary frames are audio, not control messages
    try:
        event = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(event, dict) or "type" not in event:
        return None
    return ControlMessage(type=str(event["type"]))


def save_message(ctx: WorkerContext, interview_id: uuid.UUID, role: Role, text: str) -> None:
    with ctx.database.session() as s:
        s.add(Message(interview_id=interview_id, role=role, content=text))
        s.commit()


def load_history(ctx: WorkerContext, interview_id: uuid.UUID) -> list[tuple[Role, str]]:
    with ctx.database.session() as s:
        interview = s.get(Interview, interview_id)
        if interview is None:
            return []
        return [(m.role, m.content) for m in interview.messages]


def finalize(ctx: WorkerContext, interview_id: uuid.UUID) -> None:
    with ctx.database.session() as s:
        moved = transition(
            s, interview_id, [InterviewStatus.IN_PROGRESS], InterviewStatus.EVALUATING,
            ended_at=utcnow(),
        )
    if moved:
        ctx.tasks.evaluate(interview_id)
        log.info("interview %s finished; evaluation queued", interview_id)


class VoiceSession:
    """Orchestrates one interview's voice call over a single browser WebSocket."""

    def __init__(
        self,
        ctx: WorkerContext,
        r: aioredis.Redis,
        interview_id: uuid.UUID,
        browser_ws: ServerConnection,
    ) -> None:
        self.ctx = ctx
        self.r = r
        self.interview_id = interview_id
        self.browser_ws = browser_ws
        self.shutting_down = False
        self.ending = False
        self._dg_ws = None  # type: ignore[var-annotated]
        self._history: list[tuple[Role, str]] = []

    async def run(self) -> None:
        settings = self.ctx.settings
        interview = await asyncio.to_thread(self._load_interview)
        if interview is None:
            await self.browser_ws.close(code=4404, reason="interview not found")
            return

        started_at = interview.started_at or utcnow()
        if started_at.tzinfo is None:  # SQLite drops tz info
            started_at = started_at.replace(tzinfo=UTC)
        deadline = started_at + timedelta(seconds=settings.max_call_seconds)
        self._history = await asyncio.to_thread(load_history, self.ctx, self.interview_id)

        system_prompt = interviewer.build_system_prompt(
            interview.github_metadata or {}, settings.max_call_seconds
        )

        watcher: asyncio.Task[None] | None = None
        try:
            self._dg_ws = await stt.connect_stream(settings)
            dg_task = asyncio.create_task(self._pump_transcripts(system_prompt))
            watcher = asyncio.create_task(self._watch(deadline, system_prompt))

            if not self._history:
                # First turn: greet the candidate without waiting on their audio.
                await self._generate_and_speak(system_prompt, interviewer.GREETING_INSTRUCTION)

            async for raw in self.browser_ws:
                await self._on_browser_message(raw)
        except (ConnectionClosed, OSError, TimeoutError, SttError) as exc:
            log.info("interview %s: voice session closed: %s", self.interview_id, exc)
        finally:
            if watcher:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher
            if self._dg_ws is not None:
                with contextlib.suppress(Exception):
                    await stt.close_stream(self._dg_ws)
            with contextlib.suppress(NameError, asyncio.CancelledError):
                dg_task.cancel()
                await dg_task
            if not self.shutting_down:
                await self.r.delete(end_key(self.interview_id))
                await asyncio.to_thread(finalize, self.ctx, self.interview_id)

    def _load_interview(self) -> Interview | None:
        with self.ctx.database.session() as s:
            interview = s.get(Interview, self.interview_id)
            if interview is None:
                return None
            _ = interview.github_metadata, interview.started_at  # load before session closes
            return interview

    async def _on_browser_message(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            if self._dg_ws is not None:
                with contextlib.suppress(ConnectionClosed):
                    await self._dg_ws.send(raw)
            return
        msg = parse_control_message(raw)
        if msg and msg.type == "end":
            self.ending = True
            with contextlib.suppress(ConnectionClosed):
                await self.browser_ws.close(code=1000, reason="end requested")

    async def _pump_transcripts(self, system_prompt: str) -> None:
        """Forward finalized Deepgram transcripts into the LLM -> TTS pipeline."""
        assert self._dg_ws is not None
        async for chunk in stt.iter_transcripts(self._dg_ws):
            if not chunk.speech_final:
                continue
            await asyncio.to_thread(
                save_message, self.ctx, self.interview_id, Role.USER, chunk.text
            )
            self._history.append((Role.USER, chunk.text))
            await self._generate_and_speak(system_prompt, None)

    async def _generate_and_speak(self, system_prompt: str, instruction: str | None) -> None:
        try:
            reply = await asyncio.to_thread(
                interviewer.next_turn,
                self.ctx.settings,
                system_prompt,
                self._history,
                user_instruction=instruction,
            )
        except interviewer.InterviewerError as exc:
            log.warning("interview %s: interviewer LLM failed: %s", self.interview_id, exc)
            return

        await asyncio.to_thread(save_message, self.ctx, self.interview_id, Role.ASSISTANT, reply)
        self._history.append((Role.ASSISTANT, reply))
        with contextlib.suppress(ConnectionClosed):
            payload = json.dumps({"type": "transcript", "role": "assistant", "text": reply})
            await self.browser_ws.send(payload)

        try:
            audio = await asyncio.to_thread(tts.synthesize, self.ctx.settings, reply)
        except tts.TtsError as exc:
            log.warning("interview %s: tts failed: %s", self.interview_id, exc)
            return
        with contextlib.suppress(ConnectionClosed):
            await self.browser_ws.send(audio)

    async def _watch(self, deadline, system_prompt: str) -> None:  # type: ignore[no-untyped-def]
        wrapped_up = False
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
            now = utcnow()
            if await self.r.exists(end_key(self.interview_id)):
                log.info("interview %s: end requested", self.interview_id)
                break
            if self.ending:
                break
            if now >= deadline:
                log.info("interview %s: time limit reached", self.interview_id)
                break
            if not wrapped_up and (deadline - now).total_seconds() <= WRAP_UP_LEAD_SECONDS:
                wrapped_up = True
                await self._generate_and_speak(system_prompt, interviewer.WRAP_UP_INSTRUCTION)
        with contextlib.suppress(ConnectionClosed):
            await self.browser_ws.close(code=1000, reason="interview ended")


class VoiceSessionWorker:
    """Serves the browser-facing WebSocket and tracks one VoiceSession per interview."""

    def __init__(self, ctx: WorkerContext) -> None:
        self.ctx = ctx
        self.r = aioredis.from_url(ctx.settings.redis_url)
        self.sessions: dict[uuid.UUID, asyncio.Task[None]] = {}

    @staticmethod
    def _interview_id_from_path(path: str) -> uuid.UUID | None:
        parts = [p for p in path.split("/") if p]
        if len(parts) != 2 or parts[0] != "voice":
            return None
        try:
            return uuid.UUID(parts[1])
        except ValueError:
            return None

    def _claim(self, interview_id: uuid.UUID) -> bool:
        """Mark the interview's voice session as started (PRE -> IN_PROGRESS), or confirm
        it's already the live in_progress call we're attaching to."""
        with self.ctx.database.session() as s:
            interview = s.get(Interview, interview_id)
            if interview is None:
                return False
            if interview.status == InterviewStatus.IN_PROGRESS:
                return True
            if interview.status != InterviewStatus.PRE or interview.github_metadata is None:
                return False
            return transition(
                s, interview_id, [InterviewStatus.PRE], InterviewStatus.IN_PROGRESS,
                started_at=utcnow(), call_id=str(interview_id),
            )

    async def handler(self, ws: ServerConnection) -> None:
        interview_id = self._interview_id_from_path(ws.request.path)
        if interview_id is None:
            await ws.close(code=4400, reason="bad path")
            return
        if interview_id in self.sessions:
            await ws.close(code=4409, reason="interview already has a live session")
            return
        claimed = await asyncio.to_thread(self._claim, interview_id)
        if not claimed:
            await ws.close(code=4409, reason="interview is not ready to start")
            return

        session = VoiceSession(self.ctx, self.r, interview_id, ws)
        task = asyncio.current_task()
        if task is not None:
            self.sessions[interview_id] = task
        try:
            await session.run()
        finally:
            self.sessions.pop(interview_id, None)

    def _recoverable(self) -> list[tuple[uuid.UUID, str | None]]:
        with self.ctx.database.session() as s:
            rows = s.execute(
                select(Interview.id, Interview.started_at).where(
                    Interview.status == InterviewStatus.IN_PROGRESS
                )
            ).all()
        return [tuple(r) for r in rows]

    async def fail_stale_orphans(self) -> None:
        """On startup, fail interviews that were claimed but never got a live session."""
        now = utcnow()
        for iid, started_at in await asyncio.to_thread(self._recoverable):
            started_at = started_at or now
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=UTC)
            if (now - started_at).total_seconds() > ORPHAN_CLAIM_SECONDS:
                await asyncio.to_thread(self._fail_or_finalize, iid)

    def _fail_or_finalize(self, iid: uuid.UUID) -> None:
        with self.ctx.database.session() as s:
            transition(
                s, iid, [InterviewStatus.IN_PROGRESS], InterviewStatus.FAILED,
                error="The voice session was interrupted and could not resume.",
                ended_at=utcnow(),
            )

    async def shutdown(self) -> None:
        for task in list(self.sessions.values()):
            task.cancel()
        await asyncio.gather(*self.sessions.values(), return_exceptions=True)
        await self.r.aclose()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ctx = get_context()
    worker = VoiceSessionWorker(ctx)
    await worker.fail_stale_orphans()

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    async with serve(worker.handler, ctx.settings.voice_ws_host, ctx.settings.voice_ws_port):
        log.info(
            "voice session worker listening on %s:%d",
            ctx.settings.voice_ws_host, ctx.settings.voice_ws_port,
        )
        await stop.wait()
        log.info("shutting down")
        await worker.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
