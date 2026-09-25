import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import contact as contact_route
from app.store import MemoryStore, use_store

EMAIL = "xamidovasadbek.dev@gmail.com"
CODE = "test-recovery-code"
REPO = "xamidovasadbekdev-arch/Personal_blog"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    use_store(MemoryStore())
    monkeypatch.setenv("ADMIN_EMAIL", EMAIL)
    monkeypatch.setenv("ADMIN_SETUP_CODE", CODE)
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


@pytest.fixture
def client():
    return TestClient(app)


def ip(address):
    return {"x-forwarded-for": address}


def setup(client, password="first-password-1", address="1.1.1.1"):
    return client.post("/setup", json={"email": EMAIL, "code": CODE, "password": password}, headers=ip(address))


def login(client, password="first-password-1", email=EMAIL, address="1.1.1.1"):
    return client.post("/login", json={"email": email, "password": password}, headers=ip(address))


# ---- Sign-in -----------------------------------------------------------------

def test_health(client):
    assert client.get("/").json()["status"] == "ok"


def test_login_before_any_password(client):
    assert login(client).status_code == 409


def test_setup_rejects_wrong_code_and_short_password(client):
    wrong = client.post("/setup", json={"email": EMAIL, "code": "nope", "password": "long-enough-1"})
    assert wrong.status_code == 401
    assert setup(client, password="short").status_code == 422


def test_setup_disabled_without_code(client, monkeypatch):
    monkeypatch.setenv("ADMIN_SETUP_CODE", "")
    assert setup(client).status_code == 403


def test_login_flow(client):
    assert setup(client).status_code == 200
    assert login(client, password="wrong").status_code == 401
    assert login(client, email="other@example.com").status_code == 401
    ok = login(client, email="  XamidovAsadbek.dev@Gmail.com ")
    assert ok.status_code == 200
    assert ok.json()["token"].startswith("sess.")


def test_password_is_stored_hashed(client):
    from app.store import get_store

    setup(client)
    stored = get_store().hgetall("admin")
    assert stored["hash"].startswith("$2")
    assert "first-password-1" not in str(stored)


def test_lockout_after_five_failures(client):
    setup(client)
    for _ in range(5):
        login(client, password="wrong", address="6.6.6.6")
    assert login(client, address="6.6.6.6").status_code == 429
    assert login(client, address="7.7.7.7").status_code == 200


def test_change_password_signs_out_old_sessions(client):
    setup(client)
    token = login(client).json()["token"]
    auth = {"authorization": f"token {token}"}
    wrong = client.post("/password", json={"email": EMAIL, "current": "nope", "next": "second-password-2"})
    assert wrong.status_code == 401
    changed = client.post("/password", json={"email": EMAIL, "current": "first-password-1", "next": "second-password-2"})
    assert changed.status_code == 200
    assert client.get(f"/gh/api/v3/repos/{REPO}", headers=auth).status_code == 401
    assert login(client, address="3.3.3.3").status_code == 401
    assert login(client, password="second-password-2", address="3.3.3.3").status_code == 200


def test_auth_window_lists_allowed_origins(client):
    page = client.get("/auth")
    assert page.status_code == 200
    assert "https://xamidovasadbek.dev" in page.text
    assert page.headers["x-frame-options"] == "DENY"


def test_cors_allows_site_only(client):
    allowed = client.options("/login", headers={"Origin": "https://xamidovasadbek.dev", "Access-Control-Request-Method": "POST"})
    assert allowed.headers.get("access-control-allow-origin") == "https://xamidovasadbek.dev"
    other = client.options("/login", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in other.headers


# ---- GitHub proxy --------------------------------------------------------------

def test_proxy_requires_session(client):
    assert client.get(f"/gh/api/v3/repos/{REPO}").status_code == 401
    assert client.get(f"/gh/api/v3/repos/{REPO}", headers={"authorization": "token sess.a.b"}).status_code == 401


def test_proxy_only_reaches_site_repo(client):
    setup(client)
    auth = {"authorization": f"token {login(client).json()['token']}"}
    assert client.get("/gh/api/v3/repos/torvalds/linux", headers=auth).status_code == 404
    assert client.get("/gh/api/v3/orgs/github", headers=auth).status_code == 404


def test_proxy_forwards_to_github(client):
    """Hits the real GitHub API (public repo, no token needed for this read)."""
    setup(client)
    auth = {"authorization": f"token {login(client).json()['token']}"}
    response = client.get(f"/gh/api/v3/repos/{REPO}?per_page=1", headers=auth)
    assert response.status_code == 200
    assert response.json()["full_name"] == REPO


# ---- Contact form --------------------------------------------------------------

@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(contact_route, "send_email", lambda **kwargs: messages.append(kwargs))
    return messages


MESSAGE = {"name": "Ali Valiyev", "email": "ali@example.com", "subject": "Project", "message": "Hello <b>there</b>"}


def test_contact_sends_email(client, sent):
    response = client.post("/contact", json=MESSAGE)
    assert response.status_code == 200
    assert sent[0]["reply_to"] == "ali@example.com"
    assert "[xamidov.dev] Project" == sent[0]["subject"]
    assert "&lt;b&gt;" in sent[0]["html_body"]  # user input is escaped in the HTML email


def test_contact_validation(client, sent):
    assert client.post("/contact", json={**MESSAGE, "email": "not-an-email"}).status_code == 422
    assert client.post("/contact", json={**MESSAGE, "message": ""}).status_code == 422
    assert client.post("/contact", json={**MESSAGE, "message": "x" * 5001}).status_code == 422
    assert sent == []


def test_contact_honeypot_is_silently_dropped(client, sent):
    assert client.post("/contact", json={**MESSAGE, "website": "spam.example"}).status_code == 200
    assert sent == []


def test_contact_rate_limit(client, sent):
    for _ in range(5):
        assert client.post("/contact", json=MESSAGE, headers=ip("8.8.8.8")).status_code == 200
    assert client.post("/contact", json=MESSAGE, headers=ip("8.8.8.8")).status_code == 429
    assert client.post("/contact", json=MESSAGE, headers=ip("9.9.9.9")).status_code == 200


def test_contact_not_configured(client, sent, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "")
    assert client.post("/contact", json=MESSAGE).status_code == 503


def test_contact_email_failure(client, monkeypatch):
    import httpx

    def fail(**kwargs):
        raise httpx.HTTPError("resend down")

    monkeypatch.setattr(contact_route, "send_email", fail)
    assert client.post("/contact", json=MESSAGE).status_code == 502


def test_empty_settings_fall_back_to_defaults(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("ALLOWED_ORIGINS", "")
    monkeypatch.setenv("CONTACT_FROM", "")
    settings = get_settings()
    assert "https://xamidovasadbek.dev" in settings.allowed_origins
    assert "resend.dev" in settings.contact_from


def test_origins_tolerate_quotes_and_slashes(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("ALLOWED_ORIGINS", '"https://xamidovasadbek.dev/", https://www.xamidovasadbek.dev')
    assert get_settings().allowed_origins == ["https://xamidovasadbek.dev", "https://www.xamidovasadbek.dev"]


def test_redis_found_under_any_prefix(monkeypatch):
    from app.config import get_settings

    for name in ("KV_REST_API_URL", "KV_REST_API_TOKEN", "UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STORAGE_REST_API_URL", "https://example.upstash.io")
    monkeypatch.setenv("STORAGE_REST_API_TOKEN", "tok")
    monkeypatch.setenv("STORAGE_REST_API_READ_ONLY_TOKEN", "ro")
    settings = get_settings()
    assert (settings.redis_url, settings.redis_token) == ("https://example.upstash.io", "tok")


def test_health_reports_configuration_without_values(client):
    body = client.get("/").json()
    assert body["configured"]["admin_email"] is True
    assert "xamidovasadbek" not in str(body)
