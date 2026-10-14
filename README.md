# AI Interviewer — Flask port

A voice-based, GitHub-aware technical interview app. See `PLAN.md` for the
original app's design and the rationale for each change, and `INFRA.md` for deployment details.

## What it does

1. You submit a GitHub profile. The app fetches your public repos (background job).
2. You start a voice call: the browser captures your microphone and streams audio over a
   WebSocket to our **voice session worker**. The worker streams that audio to **Deepgram**
   for speech-to-text, sends the transcript plus conversation history to **OpenAI** (plain
   chat completions) to generate the interviewer's next line, and synthesizes that reply with
   **Sarvam** text-to-speech, streaming the audio back to the browser over the same socket.
3. The voice session worker persists both sides of the transcript as it goes, feeds the
   interviewer prompt your GitHub summary as context, and enforces the call time limit.
4. When the call ends, a background job sends the transcript to an LLM (OpenAI JSON mode or
   Amazon Bedrock) for a score and feedback.

## Project layout

```
app/            Flask app: blueprints (pages, api), models, config, schemas, templates/static
                app/services/stt.py (Deepgram), tts.py (Sarvam), interviewer.py (OpenAI chat)
worker/         voice_session.py (long-lived voice WebSocket worker), jobs.py (RQ: github fetch, evaluate)
migrations/     Alembic
tests/          pytest; fakeredis + SQLite, no network calls
deploy/         nginx config (production TLS + local/compose variants), systemd units
Dockerfile, compose.yaml
```

## Local setup — full stack via Docker Compose (recommended)

This runs every service — Postgres, Redis, the Flask app, the RQ worker, the voice session
worker, and an nginx reverse proxy — as containers managed by `docker compose`, each with
`restart: unless-stopped`. nginx puts the Flask app and the voice WebSocket on **one port**
(`127.0.0.1:8080`), which matters if you're developing behind a tunnel/port-forward (VS Code
Remote Tunnels, SSH port forwarding, etc.) that only exposes one port cleanly per hostname —
with everything behind a single origin, there's no second port/hostname for the browser to
resolve, and no mixed-content/CORS issues either.

```bash
cp .env.example .env && chmod 600 .env
```

Fill in `.env`:
- `SECRET_KEY`, `OPENAI_API_KEY` (or the Azure OpenAI vars), `DEEPGRAM_API_KEY`, `SARVAM_API_KEY`
- `POSTGRES_PASSWORD` — any value; it's used by both the `postgres` container and `DATABASE_URL`
- Leave `DATABASE_URL`, `REDIS_URL`, `RATELIMIT_STORAGE_URI` as shipped in `.env.example` —
  they already point at the compose service names (`postgres`, `redis`), not `127.0.0.1`
- Leave `VOICE_WS_PUBLIC_URL` empty — nginx proxies `/voice/` on the same origin as the page,
  so the browser just uses a same-origin relative path (see `app/static/js/interview.js`)

```bash
docker compose up --build -d
docker compose ps                 # postgres, redis, web should show (healthy); jobs,
                                   # voice-session, nginx should show Up
docker compose logs -f            # tail everything; Ctrl-C to stop tailing (containers keep running)
```

Open `http://localhost:8080` (or `http://<ec2-ip>:8080`, or whatever hostname/port your
tunnel forwards `127.0.0.1:8080` to). If you're using a tunnel (VS Code Ports panel, ngrok,
etc.), forward **port 8080 only** — that's the single origin everything is served from.

The `migrate` service runs `alembic upgrade head` automatically on `up` and exits; `web`,
`jobs`, and `voice-session` all wait for it to finish before starting, so there's no need to
run migrations by hand.

To stop everything: `docker compose down` (add `-v` to also drop the Postgres/Redis volumes
— this deletes all interview data, so only do that if you mean to start fresh).

### Running against the Docker Compose stack without rebuilding

If you're actively editing `app/`/`worker/` code and don't want to rebuild the image on every
change, run only the data stores in Docker and the app directly on the host instead — see the
next section. Just make sure `.env`'s `DATABASE_URL`/`REDIS_URL`/`RATELIMIT_STORAGE_URI` point
at `127.0.0.1` in that case, not the compose service names.

## Local setup — running processes directly on the host

```bash
~/.local/bin/uv sync --all-extras   # drop --all-extras if you don't need the Bedrock evaluator
cp .env.example .env && chmod 600 .env   # fill in SECRET_KEY, OPENAI_API_KEY, DEEPGRAM_API_KEY, SARVAM_API_KEY, ...
```

In `.env`, point the data stores at `127.0.0.1` instead of the compose service names:
```
DATABASE_URL=postgresql+psycopg://ai_interviewer:<password>@127.0.0.1:5432/ai_interviewer
REDIS_URL=redis://127.0.0.1:6379/0
RATELIMIT_STORAGE_URI=redis://127.0.0.1:6379/1
```
(or `DATABASE_URL=sqlite:///ai_interviewer.db` if you don't want to run Postgres at all).

```bash
# Postgres + Redis only, via Docker:
docker compose up -d postgres redis

.venv/bin/alembic upgrade head

# three processes, in separate terminals:
.venv/bin/flask --app wsgi run --debug          # web (or: gunicorn wsgi:app)
.venv/bin/rq worker default                     # background jobs
.venv/bin/python -m worker.voice_session        # voice session worker (STT/LLM/TTS pipeline)
```

Open `http://localhost:5000`. Microphone access requires HTTPS everywhere except `localhost`.

With no reverse proxy in front of this setup, the voice WebSocket (port 8765) is a different
port than the Flask app (port 5000), so the browser can't resolve it from the page's own
origin. On plain `localhost` this still works because `ws://localhost:8765` is directly
reachable from your own machine. If you're accessing the app through anything other than
`localhost` relative to where the browser runs — a tunnel, a forwarded port, a remote IP —
set `VOICE_WS_PUBLIC_URL` in `.env` to the URL your browser can actually use to reach port
8765 (e.g. `ws://localhost:8765` if your tunnel forwards that port too, or a second forwarded
tunnel URL with `wss://`). Otherwise you'll see "Could not connect to the voice server." Using
the full Docker Compose stack above avoids this entirely, since nginx puts both on one origin.

## Tests

```bash
.venv/bin/pytest       # API, validation, evaluator parsing, voice session pipeline
.venv/bin/ruff check .
```

The voice session tests run real local WebSocket servers (via the `websockets` library) to
exercise `VoiceSession` end-to-end — including a fake Deepgram server — with the OpenAI and
Sarvam calls stubbed out. No network access or real API credentials are used.

## Status / what's implemented

Done: Flask app factory, DB models + Alembic migration, GitHub fetch job, voice session
WebSocket worker (Deepgram STT + OpenAI chat completions + Sarvam TTS, with transcript
persistence/dedupe/time-limit), evaluator (OpenAI + Bedrock), result polling, rate limiting,
security headers, CSP, ownership-scoped access (no public enumeration of other people's
interviews), Docker/compose (including an nginx container for a single-origin local stack —
see `deploy/nginx.compose.conf`), systemd units, nginx config, retention CLI command, full
test suite.

`docker compose` requires the Docker Compose CLI plugin (`docker compose version`). If it's
missing (`docker: 'compose' is not a docker command`), install the plugin into
`/usr/libexec/docker/cli-plugins/` or `~/.docker/cli-plugins/` — see
[Docker's docs](https://docs.docker.com/compose/install/linux/).

Not implemented (see "Open decisions" in `PLAN.md`): user accounts/OAuth, audio recording/storage,
an admin view, and CI (GitHub Actions) — add `.github/workflows/ci.yml` running `ruff` + `pytest`
when you're ready to wire this to a repo.

## Security notes

- `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, `SARVAM_API_KEY`, and `GITHUB_TOKEN` are server-side
  only; the browser only ever talks to our `/api/v1/*` endpoints and the `/voice/<id>`
  WebSocket, both served from our own origin.
- GitHub and transcript text are treated as untrusted input and wrapped in delimiters before
  being placed in any LLM prompt.
- Per-interview access is scoped to the browser session that created it (signed cookie), not by
  a guessable ID alone.
- Rate limits: per-IP daily interview cap, per-day global cap (cost guard), 5/minute on session
  creation.
- No endpoint in this app can merge, push, or modify git history — it only talks to OpenAI,
  Deepgram, Sarvam, GitHub's public read API, and its own database.

