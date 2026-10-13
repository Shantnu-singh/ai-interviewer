import uuid

from app.models import Interview, InterviewStatus


def create(client, github="octocat"):
    return client.post("/api/v1/interviews", json={"github": github})


def mark_ready(database, interview_id):
    with database.session() as s:
        iv = s.get(Interview, uuid.UUID(interview_id))
        iv.github_metadata = {"login": "octocat", "repos": []}
        s.commit()


def status_of(database, interview_id):
    with database.session() as s:
        return s.get(Interview, uuid.UUID(interview_id)).status


def test_pages_render_with_security_headers(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "script-src 'self'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["Permissions-Policy"].startswith("microphone=(self)")


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200 and resp.get_json()["db"] is True


def test_create_interview(client, fake_tasks):
    resp = create(client, "https://github.com/octocat/")
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["status"] == "pre"
    assert fake_tasks.calls == [("fetch_github", uuid.UUID(body["id"]))]
    assert client.get(f"/interview/{body['id']}").status_code == 200


def test_create_rejects_bad_input(client):
    assert create(client, "https://evil.com/x").status_code == 422
    assert client.post("/api/v1/interviews", data="github=x").status_code == 415


def test_other_browser_cannot_access(app, client):
    iid = create(client).get_json()["id"]
    other = app.test_client()
    assert other.get(f"/api/v1/interviews/{iid}").status_code == 404
    assert other.get(f"/api/v1/interviews/{iid}/result").status_code == 404
    assert other.get(f"/result/{iid}").status_code == 404


def test_per_ip_rate_limit(client):
    for _ in range(3):
        assert create(client).status_code == 201
    assert create(client).status_code == 429


def test_daily_cap(app, client, make_settings):
    app.extensions["settings"] = make_settings(max_interviews_per_day=1)
    assert create(client).status_code == 201
    assert create(client).status_code == 503


def test_session_requires_ready_profile(client):
    iid = create(client).get_json()["id"]
    resp = client.post(f"/api/v1/interviews/{iid}/session")
    assert resp.status_code == 409


def test_session_flow(client, database):
    iid = create(client).get_json()["id"]
    assert client.get(f"/api/v1/interviews/{iid}").get_json()["ready"] is False
    mark_ready(database, iid)
    assert client.get(f"/api/v1/interviews/{iid}").get_json()["ready"] is True

    resp = client.post(f"/api/v1/interviews/{iid}/session")
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["ws_path"] == f"/voice/{iid}"
    # No VOICE_WS_PUBLIC_URL configured -> browser should fall back to a same-origin
    # relative path (works in production, where nginx proxies /voice/ for us).
    assert body["ws_url"] is None
    # Flask no longer claims the interview itself -- the voice session worker does, when the
    # browser actually connects. Asking for a session twice is fine; it's not a real claim.
    assert status_of(database, iid) == InterviewStatus.PRE

    again = client.post(f"/api/v1/interviews/{iid}/session")
    assert again.status_code == 201

    resp = client.post(f"/api/v1/interviews/{iid}/end", json={})
    assert resp.status_code == 202


def test_session_uses_voice_ws_public_url_when_configured(client, database, app, make_settings):
    # Local dev: Flask's dev server doesn't proxy /voice/ to the separate worker process,
    # so the browser needs an absolute URL pointing at the worker directly.
    app.extensions["settings"] = make_settings(voice_ws_public_url="ws://localhost:8765")
    iid = create(client).get_json()["id"]
    mark_ready(database, iid)

    resp = client.post(f"/api/v1/interviews/{iid}/session")
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["ws_url"] == f"ws://localhost:8765/voice/{iid}"


def test_end_before_start_fails_interview(client, database):
    iid = create(client).get_json()["id"]
    assert client.post(f"/api/v1/interviews/{iid}/end", json={}).status_code == 202
    assert status_of(database, iid) == InterviewStatus.FAILED
    body = client.get(f"/api/v1/interviews/{iid}/result").get_json()
    assert body["status"] == "failed" and body["error"]


def test_end_signals_in_progress_interview(client, database, fake_tasks):
    iid = create(client).get_json()["id"]
    with database.session() as s:
        iv = s.get(Interview, uuid.UUID(iid))
        iv.status = InterviewStatus.IN_PROGRESS
        s.commit()
    resp = client.post(f"/api/v1/interviews/{iid}/end", json={})
    assert resp.status_code == 202
    assert ("signal_end", uuid.UUID(iid)) in fake_tasks.calls
