import asyncio
import json
import uuid
from dataclasses import replace
from datetime import timedelta

import fakeredis
import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from app.models import Interview, InterviewStatus, Message, Role, utcnow
from app.schemas import Evaluation
from app.services import github as github_service
from app.services import interviewer, stt, tts
from app.services.evaluator import EvaluationError
from worker import jobs, voice_session


def new_interview(database, status=InterviewStatus.PRE, **kw):
    with database.session() as s:
        iv = Interview(github_username="octocat", client_ip_hash="h", status=status, **kw)
        s.add(iv)
        s.commit()
        return iv.id


def get(database, iid):
    with database.session() as s:
        iv = s.get(Interview, iid)
        _ = list(iv.messages)
        return iv


def add_messages(database, iid, *pairs):
    with database.session() as s:
        for role, text in pairs:
            s.add(Message(interview_id=iid, role=role, content=text))
        s.commit()


# ---------- fetch_github ----------

def test_fetch_github_stores_and_caches(worker_ctx, database, monkeypatch):
    calls = []
    monkeypatch.setattr(
        jobs, "fetch_profile", lambda u, t: calls.append(u) or {"login": u, "repos": []}
    )
    a = new_interview(database)
    jobs.fetch_github(str(a), worker_ctx)
    assert get(database, a).github_metadata == {"login": "octocat", "repos": []}

    b = new_interview(database)
    jobs.fetch_github(str(b), worker_ctx)
    assert calls == ["octocat"]  # second one served from cache
    assert get(database, b).github_metadata is not None


def test_fetch_github_user_not_found(worker_ctx, database, monkeypatch):
    def missing(u, t):
        raise github_service.GitHubUserNotFound(u)

    monkeypatch.setattr(jobs, "fetch_profile", missing)
    iid = new_interview(database)
    jobs.fetch_github(str(iid), worker_ctx)
    iv = get(database, iid)
    assert iv.status == InterviewStatus.FAILED and "not found" in iv.error


# ---------- evaluate ----------

class StubEvaluator:
    def __init__(self, results):
        self.results = list(results)

    def evaluate(self, transcript, meta):
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        assert "Candidate:" in transcript
        return r


LONG_ANSWER = (
    "I built a distributed cache using consistent hashing with virtual nodes, "
    "replication, and read repair to handle node failures"
)


def test_evaluate_success_after_retry(worker_ctx, database, monkeypatch):
    stub = StubEvaluator([EvaluationError("bad json"), Evaluation(score=8, feedback="Nice")])
    monkeypatch.setattr(jobs, "get_evaluator", lambda s: stub)
    iid = new_interview(database, InterviewStatus.EVALUATING)
    add_messages(
        database, iid, (Role.ASSISTANT, "Tell me about a project"), (Role.USER, LONG_ANSWER)
    )
    jobs.evaluate(str(iid), worker_ctx)
    iv = get(database, iid)
    assert iv.status == InterviewStatus.DONE and iv.score == 8 and iv.feedback == "Nice"


def test_evaluate_too_short_skips_llm(worker_ctx, database, monkeypatch):
    monkeypatch.setattr(jobs, "get_evaluator", lambda s: pytest.fail("LLM should not be called"))
    iid = new_interview(database, InterviewStatus.EVALUATING)
    add_messages(database, iid, (Role.USER, "hi"))
    jobs.evaluate(str(iid), worker_ctx)
    iv = get(database, iid)
    assert iv.status == InterviewStatus.DONE and iv.score == 0


def test_evaluate_gives_up(worker_ctx, database, monkeypatch):
    stub = StubEvaluator([EvaluationError("x"), EvaluationError("y")])
    monkeypatch.setattr(jobs, "get_evaluator", lambda s: stub)
    iid = new_interview(database, InterviewStatus.EVALUATING)
    add_messages(database, iid, (Role.USER, LONG_ANSWER))
    jobs.evaluate(str(iid), worker_ctx)
    assert get(database, iid).status == InterviewStatus.FAILED


def test_evaluate_ignores_wrong_state(worker_ctx, database, monkeypatch):
    monkeypatch.setattr(jobs, "get_evaluator", lambda s: pytest.fail("should not run"))
    iid = new_interview(database, InterviewStatus.DONE)
    jobs.evaluate(str(iid), worker_ctx)


# ---------- stt.parse_message (pure) ----------

def test_stt_parse_message_extracts_final_transcript():
    event = {
        "type": "Results",
        "is_final": True,
        "speech_final": True,
        "channel": {"alternatives": [{"transcript": " I use Python. "}]},
    }
    chunk = stt.parse_message(json.dumps(event))
    assert chunk == stt.TranscriptChunk("I use Python.", True, True)


def test_stt_parse_message_ignores_empty_and_other_types():
    assert stt.parse_message(json.dumps({"type": "Metadata"})) is None
    assert stt.parse_message(
        json.dumps({"type": "Results", "channel": {"alternatives": [{"transcript": ""}]}})
    ) is None
    assert stt.parse_message("not json") is None


# ---------- voice_session.parse_control_message (pure) ----------

def test_parse_control_message():
    assert voice_session.parse_control_message(json.dumps({"type": "end"})) == (
        voice_session.ControlMessage("end")
    )
    assert voice_session.parse_control_message(b"\x00\x01binary-audio") is None
    assert voice_session.parse_control_message("not json") is None
    assert voice_session.parse_control_message(json.dumps({"no": "type"})) is None


# ---------- finalize ----------

def test_finalize_is_idempotent(worker_ctx, database, fake_tasks):
    iid = new_interview(database, InterviewStatus.IN_PROGRESS)
    voice_session.finalize(worker_ctx, iid)
    voice_session.finalize(worker_ctx, iid)
    assert get(database, iid).status == InterviewStatus.EVALUATING
    assert fake_tasks.calls.count(("evaluate", iid)) == 1


# ---------- VoiceSession end-to-end against fake Deepgram + stubbed LLM/TTS ----------

SILENCE_WAV = (
    b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
    + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + (16000).to_bytes(4, "little")
    + (32000).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
    + b"data" + (0).to_bytes(4, "little")
)


def test_voice_session_end_to_end(worker_ctx, database, fake_tasks, monkeypatch):
    """Browser <-> VoiceSession <-> fake Deepgram, with the LLM and TTS stubbed out."""
    iid = new_interview(
        database, InterviewStatus.IN_PROGRESS, call_id=str(uuid.uuid4()), started_at=utcnow()
    )

    monkeypatch.setattr(
        interviewer, "next_turn",
        lambda settings, prompt, history, user_instruction=None: (
            "Welcome!" if user_instruction else "Thanks, got it."
        ),
    )
    monkeypatch.setattr(tts, "synthesize", lambda settings, text: SILENCE_WAV)

    dg_received: list[bytes] = []

    async def dg_handler(ws):
        async for raw in ws:
            if isinstance(raw, bytes):
                dg_received.append(raw)
                # Immediately "finalize" a transcript once we see any audio.
                await ws.send(json.dumps({
                    "type": "Results", "is_final": True, "speech_final": True,
                    "channel": {"alternatives": [{"transcript": "Thanks, happy to be here."}]},
                }))
            else:
                event = json.loads(raw)
                if event.get("type") == "CloseStream":
                    await ws.close()
                    return

    async def scenario():
        async with serve(dg_handler, "127.0.0.1", 0) as dg_server:
            dg_port = dg_server.sockets[0].getsockname()[1]
            dg_url = f"ws://127.0.0.1:{dg_port}"
            ctx = replace(worker_ctx, settings=replace(worker_ctx.settings, deepgram_ws_url=dg_url))

            async def handler(ws):
                r = fakeredis.aioredis.FakeRedis()
                session = voice_session.VoiceSession(ctx, r, iid, ws)
                await asyncio.wait_for(session.run(), 10)

            async with serve(handler, "127.0.0.1", 0) as browser_server:
                b_port = browser_server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{b_port}/voice/{iid}") as client_ws:
                    # Greeting arrives first: a transcript control frame, then audio bytes.
                    greeting_text = json.loads(await client_ws.recv())
                    greeting_audio = await client_ws.recv()
                    assert greeting_text == {
                        "type": "transcript", "role": "assistant", "text": "Welcome!"
                    }
                    assert greeting_audio == SILENCE_WAV

                    # Send a chunk of "audio" to trigger the fake Deepgram's canned transcript.
                    await client_ws.send(b"\x00\x01\x02\x03")
                    reply_text = json.loads(await client_ws.recv())
                    reply_audio = await client_ws.recv()
                    assert reply_text == {
                        "type": "transcript", "role": "assistant", "text": "Thanks, got it."
                    }
                    assert reply_audio == SILENCE_WAV

                    await client_ws.send(json.dumps({"type": "end"}))

    asyncio.run(scenario())

    iv = get(database, iid)
    assert [(m.role, m.content) for m in iv.messages] == [
        (Role.ASSISTANT, "Welcome!"),
        (Role.USER, "Thanks, happy to be here."),
        (Role.ASSISTANT, "Thanks, got it."),
    ]
    assert iv.status == InterviewStatus.EVALUATING
    assert ("evaluate", iid) in fake_tasks.calls


def test_voice_session_rejects_unknown_interview(worker_ctx):
    async def scenario():
        ctx = worker_ctx

        async def handler(ws):
            r = fakeredis.aioredis.FakeRedis()
            session = voice_session.VoiceSession(ctx, r, uuid.uuid4(), ws)
            await asyncio.wait_for(session.run(), 5)

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}/voice/doesnotmatter") as client_ws:
                with pytest.raises(ConnectionClosed):
                    await client_ws.recv()
                assert client_ws.close_code == 4404

    asyncio.run(scenario())


# ---------- VoiceSessionWorker claim/routing ----------

def test_worker_interview_id_from_path():
    w = voice_session.VoiceSessionWorker.__new__(voice_session.VoiceSessionWorker)
    iid = uuid.uuid4()
    assert w._interview_id_from_path(f"/voice/{iid}") == iid
    assert w._interview_id_from_path("/voice/not-a-uuid") is None
    assert w._interview_id_from_path("/other/path") is None


def test_worker_claim_transitions_pre_to_in_progress(worker_ctx, database):
    w = voice_session.VoiceSessionWorker.__new__(voice_session.VoiceSessionWorker)
    w.ctx = worker_ctx
    iid = new_interview(database, github_metadata={"login": "octocat"})
    assert w._claim(iid) is True
    assert get(database, iid).status == InterviewStatus.IN_PROGRESS
    # A second claim while already in_progress (e.g. reconnect) succeeds too.
    assert w._claim(iid) is True


def test_worker_claim_rejects_not_ready(worker_ctx, database):
    w = voice_session.VoiceSessionWorker.__new__(voice_session.VoiceSessionWorker)
    w.ctx = worker_ctx
    iid = new_interview(database)  # no github_metadata yet
    assert w._claim(iid) is False


def test_fail_stale_orphans(worker_ctx, database):
    old = utcnow() - timedelta(minutes=10)
    orphan = new_interview(database, InterviewStatus.IN_PROGRESS, started_at=old)

    async def scenario():
        w = voice_session.VoiceSessionWorker.__new__(voice_session.VoiceSessionWorker)
        w.ctx, w.sessions = worker_ctx, {}
        await w.fail_stale_orphans()

    asyncio.run(scenario())
    assert get(database, orphan).status == InterviewStatus.FAILED
