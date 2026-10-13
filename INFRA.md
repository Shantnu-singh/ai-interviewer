# AI Interviewer (Flask port): infrastructure

## 1. Runtime topology (single EC2, same as devflow)

```
Internet ──443──► nginx (TLS, Let's Encrypt)
                    ├─ /static/*         served directly
                    └─ /*  ─► gunicorn :8000 (127.0.0.1)  [systemd: interviewer-web]
                                   │
               ┌───────────────────┼────────────────────┐
               ▼                   ▼                    ▼
         Postgres :5432      Redis :6379        interviewer-sideband (asyncio)  [systemd]
         (127.0.0.1 only)    (127.0.0.1 only)   interviewer-jobs (RQ worker)    [systemd]
                                                        │
                                          outbound 443: api.openai.com, api.github.com,
                                                        evaluator LLM endpoint
```

- Audio goes **browser ↔ OpenAI directly** over WebRTC, so the EC2 carries only SDP, small JSON, and the sideband socket. No TURN or media server is needed.
- HTTPS is mandatory: browsers block `getUserMedia` on non-localhost HTTP.

## 2. Sizing

| Load | Instance | Notes |
|---|---|---|
| Dev or demo (<10 concurrent calls) | t3.small / t4g.small | Everything on one host |
| ~100 concurrent calls | t3.medium | One sideband process can handle hundreds of sockets. The bottleneck is OpenAI cost and rate limits, not the server |
| Beyond that | Split out RDS Postgres and ElastiCache Redis; put 2+ web instances behind an ALB | The sideband worker must scale by sharding the queue |

## 3. Containers (`compose.yaml`, planned)

| Service | Image | Ports | Notes |
|---|---|---|---|
| `web` | app image, `gunicorn -w 3 -k gthread app:create_app()` | 127.0.0.1:8000 | Read-only root filesystem, non-root user |
| `sideband` | app image, `python -m worker.sideband` | none | `restart: unless-stopped` |
| `jobs` | app image, `rq worker default` | none | |
| `postgres` | postgres:16-alpine | internal only | Named volume, daily `pg_dump` |
| `redis` | redis:7-alpine | internal only | `requirepass`, AOF on |

The alternative is to run the app with uv and systemd directly (like devflow) and keep only Postgres and Redis in Docker. Both approaches work; systemd is simpler on one box.

## 4. Configuration and secrets

`.env` (chmod 600, never committed) or SSM Parameter Store SecureString:

| Key | Purpose |
|---|---|
| `OPENAI_API_KEY` | Realtime SDP relay and sideband. **Server only** |
| `EVALUATOR_PROVIDER`, `GEMINI_API_KEY` | Evaluation. With Bedrock, no key is needed because it uses the instance role |
| `GITHUB_TOKEN` | Fine-grained token with no permissions, used only for public reads and the higher rate limit |
| `DATABASE_URL`, `REDIS_URL` | |
| `SECRET_KEY` | Flask sessions and interview-token signing |
| `MAX_CALL_SECONDS` (900), `MAX_INTERVIEWS_PER_IP_PER_DAY` (3), `DAILY_SPEND_CAP_USD` | Abuse and cost controls |
| `ALLOWED_ORIGIN` | CORS/CSP origin |

## 5. Network and security

- **Security group:** inbound 443 (and 80 only for the ACME redirect) from `0.0.0.0/0`, and 22 from your IP only or no SSH at all (use SSM Session Manager). No other inbound ports.
- Postgres and Redis bind to localhost or the Docker network only.
- **nginx:**
  - TLS 1.2+ and HSTS
  - `client_max_body_size 64k`
  - `limit_req` on `/api/`
  - CSP: `default-src 'self'; connect-src 'self' https://api.openai.com; media-src 'self' blob:`
  - `Permissions-Policy: microphone=(self)`
- **IMDSv2 required, hop limit 1**, the same as the devflow note, so containers can't read instance credentials.
- **IAM:** only if you use Bedrock for evaluation. Grant `bedrock:InvokeModel` on the specific model ARN and nothing else.
- **Cost guard:** a Redis counter of estimated call-minutes per day. When the cap is hit, `/session` returns 503. Also set a usage limit in the OpenAI dashboard.
- **Privacy:** transcripts are personal data. Set a retention job that deletes interviews older than N days and publish a privacy notice on the form page.

## 6. systemd units (planned, in `deploy/`)

- `interviewer-web.service`: gunicorn, `Restart=always`
- `interviewer-sideband.service`: `Restart=always`. On start, it re-attaches to calls that are still `in_progress` and younger than `MAX_CALL_SECONDS`, or marks them `evaluating`.
- `interviewer-jobs.service`: RQ worker
- `interviewer-retention.timer`: daily cleanup and `pg_dump` to S3, with an S3 lifecycle rule

## 7. Observability

- JSON logs to journald, optionally shipped to CloudWatch through the CloudWatch agent.
- Metrics to log or expose: interviews started and completed, call duration, evaluation failures, OpenAI 4xx/5xx, and estimated spend per day.
- `/healthz` checks the DB and Redis. Add an external uptime check against it.
- Alarms: EC2 status check, disk over 80%, and 5xx rate from nginx logs.

## 8. CI/CD

- GitHub Actions: `ruff`, `mypy`, and `pytest` against a Postgres and Redis service, then build and push the image to ECR (OIDC role, no static keys).
- Deploy: pull the image on EC2 via SSM Run Command, run `alembic upgrade head`, then restart services. Sideband restarts are safe because of the re-attach logic.

## 9. Environments

| Env | Where | Differences |
|---|---|---|
| local | `docker compose up`, `http://localhost` | localhost is exempt from the HTTPS requirement for the mic |
| staging | Same EC2 on a subdomain, or a small second instance | Low spend cap |
| prod | EC2 + nginx + TLS | Backups, alarms, retention on |
