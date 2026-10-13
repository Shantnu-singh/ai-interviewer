# AI Interviewer (Flask port): plan

Reference: https://github.com/code100x/ai-interviewer (Harkirat Singh, "Building an AI Interviewer From Scratch in 3 Hours").

## 1. What the original builds

A voice-based technical interview that is tailored to a candidate's GitHub profile.

1. **Form page:** the user enters a GitHub URL. The backend pulls the user's public repos (name, description, stars) through the GitHub API and stores them on a new `Interview` row (status `Pre`).
2. **Interview page:** the browser opens a **WebRTC** connection to the **OpenAI Realtime API** (`gpt-realtime`, voice `marin`). The backend only relays the SDP offer and answer, so the OpenAI key never reaches the browser.
3. **Sideband WebSocket:** after the call is created, the backend opens its own WebSocket to the same call (`wss://api.openai.com/v1/realtime?call_id=...`). Over it, the backend:
   - sends `session.update` with the interviewer instructions plus the GitHub metadata ("ask 2–3 CS questions based on their experience")
   - listens for `response.done` and saves the AI's spoken transcript as `Assistant` messages
4. **User transcript:** the browser streams the mic to **Deepgram** over WebSocket and POSTs each transcript chunk to the backend as `User` messages.
5. **Result page:** the page polls `GET /result/:id`. On the first call, the backend sends the transcript to **Gemini** with a JSON schema and gets back `{score: 0-10, feedback}`. It stores the result and marks the interview `Done`.

Stack: Bun/Express, Prisma with Postgres, React, Tailwind, and shadcn in a Turborepo.

### Problems to fix in the port

| Original issue | Fix in the Flask version |
|---|---|
| Deepgram key hard-coded in the frontend | Drop Deepgram. Use Realtime's built-in input transcription and capture it over the sideband, so no third-party key is needed in the browser |
| Scoring runs inside `GET /result`, with no lock, so it can run twice | Run scoring in a background job, triggered once by `POST /end` and guarded by a DB status transition |
| No auth, `cors()` allows `*`, anyone can start sessions and spend credits | Signed per-interview token, rate limits, same-origin, and a per-IP or per-user session cap |
| GitHub URL parsed with `split("/")` | Strict regex for the username; call only `api.github.com` |
| Scrapes through a proxy with no token | Use an authenticated GitHub token (5000 req/h) and cache the result |
| Sideband runs in the web process, so a restart loses it | Separate worker process |
| No session time limit | Hard cap, for example 15 min, enforced by the worker |

## 2. Target architecture

```
Browser (Jinja + vanilla JS)
  │  HTTPS (nginx, TLS: required for getUserMedia)
  ▼
Flask API (gunicorn) ──── Postgres (interviews, messages)
  │   │                    ▲
  │   └── enqueue ──► Redis ──► Worker(s)
  │                             ├─ sideband: asyncio `websockets` client → OpenAI Realtime
  │                             ├─ evaluate: transcript → LLM (JSON) → score/feedback
  │                             └─ github:  fetch + summarize repos
  │
  └── SDP relay ──► api.openai.com/v1/realtime/calls
Browser ◄──── WebRTC audio (direct) ────► OpenAI Realtime
```

Why a worker: Flask/WSGI is request-scoped, but the sideband socket lives for the whole call (minutes). Holding it in a gunicorn worker would block that worker and die on reload. A small asyncio worker can hold many sockets at once.

## 3. Components

| Component | Choice | Notes |
|---|---|---|
| Web | Flask 3, gunicorn (sync or gthread) | App factory and blueprints: `interviews`, `session`, `results` |
| Validation | Pydantic v2 | Request bodies and LLM output |
| DB | Postgres 16 with SQLAlchemy 2 and Alembic | Mirrors the Prisma schema |
| Queue | Redis with RQ, or Celery if you need retries and scheduling | Jobs: `fetch_github`, `run_sideband`, `evaluate` |
| Sideband worker | Standalone asyncio process (`websockets` lib) consuming a Redis list | Not RQ, because jobs are long-lived and concurrent |
| Realtime voice | OpenAI Realtime (`gpt-realtime`) over WebRTC | Alternative: Amazon Nova Sonic on Bedrock (bidirectional stream, server-side audio, bigger change) |
| Evaluation LLM | Any JSON-mode model (Gemini, OpenAI, or Bedrock via boto3) | Behind an `Evaluator` interface |
| Frontend | Jinja templates, ~150 lines of vanilla JS for WebRTC and meters, Tailwind CDN or htmx | React is optional; the UI is 3 pages |

## 4. Data model

```
interviews
  id              uuid pk
  github_username text
  github_metadata jsonb
  status          enum(pre, in_progress, evaluating, done, failed)
  call_id         text null          -- OpenAI realtime call id
  score           int null  (0-10)
  feedback        text null
  created_at, started_at, ended_at   timestamptz
  client_ip_hash  text               -- for rate limiting / abuse

messages
  id            uuid pk
  interview_id  fk -> interviews
  role          enum(user, assistant)
  content       text
  item_id       text unique null     -- realtime item id, dedupe on replays
  created_at    timestamptz
index (interview_id, created_at)
```

Status transitions are single `UPDATE ... WHERE status = <expected>` statements. Only one evaluation can win.

## 5. API

| Method and path | Purpose |
|---|---|
| `GET /` | Form page |
| `POST /api/v1/interviews` `{github}` | Validate username (`^[A-Za-z0-9-]{1,39}$`), create the interview, enqueue `fetch_github`. Returns `{id, token}` |
| `GET /interview/<id>` | Interview page |
| `POST /api/v1/interviews/<id>/session` (body `application/sdp`, token required) | Requires status `pre` and loaded GitHub data. Relays the SDP to OpenAI with the session config (instructions, voice, input transcription enabled). Stores `call_id`, sets `in_progress`, pushes `call_id` to the sideband queue. Returns the SDP answer |
| `POST /api/v1/interviews/<id>/end` | Marks the end. The worker closes the sideband and enqueues `evaluate` |
| `GET /api/v1/interviews/<id>/result` | Read-only: status, score, feedback, transcript |
| `GET /result/<id>` | Result page; polls the endpoint above |
| `GET /healthz` | DB and Redis checks |

Put the instructions and GitHub context in the **initial session config** at `/realtime/calls`, not only in a later `session.update`. That way the model knows the context from its first word. Keep `session.update` for mid-call changes.

## 6. Sideband worker behavior

- Connect to `wss://api.openai.com/v1/realtime?call_id=<id>` with the server-side key.
- Persist events:
  - `conversation.item.input_audio_transcription.completed` → `user` message
  - `response.done` (collect `output_audio` transcripts) → `assistant` message
  - Upsert by `item_id` so reconnects don't duplicate rows.
- Enforce the max duration. On timeout, send a closing instruction, then hang up.
- On socket close or `/end`: set `evaluating` and enqueue `evaluate`.
- Treat the GitHub metadata as untrusted data inside the prompt: wrap it in delimiters and truncate descriptions, since repo descriptions can contain prompt injection.
- Verify event names against the current Realtime API docs before implementing; they have changed between beta and GA.

## 7. Evaluation

- Input: the ordered transcript plus the GitHub summary.
- Output schema (Pydantic): `score:int 0-10`, `feedback:str`, and optionally `strengths[]`, `improvements[]`, `per_question[]`.
- Validate the output. Retry once on a schema failure, then mark the interview `failed` with an error message.
- Skip the LLM call if the user transcript is empty and store `score=0` with an explanation.

## 8. Project layout (planned)

```
ai-interviewer-flask/
  app/
    __init__.py          create_app()
    config.py            env-based settings
    models.py            SQLAlchemy models
    schemas.py           Pydantic
    blueprints/{pages,interviews,session,results}.py
    services/{github,realtime,evaluator,ratelimit}.py
    templates/{base,form,interview,result}.html
    static/js/{interview,result}.js
  worker/
    sideband.py          asyncio consumer
    jobs.py              RQ jobs: fetch_github, evaluate
  migrations/            Alembic
  tests/
  pyproject.toml / uv.lock
  Dockerfile
  compose.yaml
  deploy/                nginx, systemd units
```

## 9. Implementation phases

1. **Skeleton (0.5 day):** Flask factory, config, Postgres and Alembic models, `/healthz`, compose with Postgres and Redis.
2. **Pre-interview (0.5 day):** form page, username validation, GitHub fetch job, cache.
3. **Voice call (1 day):** interview page JS (getUserMedia, RTCPeerConnection, SDP POST, audio meters), SDP relay endpoint, session config. Milestone: you can talk to the AI.
4. **Sideband worker (1 day):** Redis queue, WebSocket consumer, transcript persistence for both roles, duration cap, end handling.
5. **Evaluation and results (0.5 day):** evaluate job, status machine, result page polling.
6. **Hardening (1 day):** interview token, rate limits (Flask-Limiter on Redis), CSRF on form posts, strict CORS/CSP, per-day spend cap, structured logging, error pages.
7. **Tests (ongoing):** pytest for validation, status transitions, and the evaluator with a mocked LLM; a sideband test against a fake WebSocket server; one manual end-to-end voice test.
8. **Deploy (0.5 day):** see `INFRA.md`.

Rough total: 5–6 focused days for one developer.

## 10. Open decisions

- **Voice provider:** OpenAI Realtime (closest to the original, simplest) or Bedrock Nova Sonic (AWS-native, uses the instance role, but audio must go through your server).
- **Evaluation model:** Gemini (as in the original), OpenAI, or Bedrock.
- **User accounts:** anonymous with rate limits, or GitHub OAuth login. OAuth also proves the candidate owns the profile.
- **Recording:** store audio or only transcripts. Storing audio has privacy and retention implications.
