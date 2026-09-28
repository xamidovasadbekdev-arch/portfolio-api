import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import comments as comments_route
from app.store import MemoryStore, use_store

EMAIL = "xamidovasadbek.dev@gmail.com"
SLUG = "my-first-post"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    use_store(MemoryStore())
    monkeypatch.setenv("ADMIN_EMAIL", EMAIL)
    monkeypatch.setenv("ADMIN_SETUP_CODE", "code")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")


@pytest.fixture
def emails(monkeypatch):
    sent = []
    monkeypatch.setattr(comments_route, "send_email", lambda **kwargs: sent.append(kwargs))
    return sent


@pytest.fixture
def client():
    return TestClient(app)


def admin_headers(client):
    client.post("/setup", json={"email": EMAIL, "code": "code", "password": "admin-password-1"})
    token = client.post("/login", json={"email": EMAIL, "password": "admin-password-1"}).json()["token"]
    return {"authorization": f"token {token}"}


def post(client, name="Ali", text="Great article!", ip="1.1.1.1", **extra):
    return client.post(f"/comments/{SLUG}", json={"name": name, "text": text, **extra}, headers={"x-forwarded-for": ip})


def links(email):
    return dict(re.findall(r"(Approve|Delete): (\S+)", email["text"]))


def test_new_comment_is_pending_and_emails_owner(client, emails):
    assert post(client).status_code == 202
    assert client.get(f"/comments/{SLUG}").json()["comments"] == []
    assert len(emails) == 1
    assert "Ali" in emails[0]["subject"]
    assert set(links(emails[0])) == {"Approve", "Delete"}


def test_email_approve_link_needs_confirmation(client, emails):
    post(client)
    approve = links(emails[0])["Approve"].replace("http://testserver", "")
    # Opening the link (e.g. by an email scanner) changes nothing.
    page = client.get(approve)
    assert page.status_code == 200 and "Approve this comment?" in page.text
    assert client.get(f"/comments/{SLUG}").json()["comments"] == []
    # Pressing the button approves it.
    token = approve.split("token=")[1]
    done = client.post("/moderate", data={"token": token})
    assert "approved" in done.text
    shown = client.get(f"/comments/{SLUG}").json()["comments"]
    assert [c["name"] for c in shown] == ["Ali"]
    assert set(shown[0]) == {"id", "name", "text", "created"}  # no status/ip leaked


def test_email_delete_link(client, emails):
    post(client)
    token = links(emails[0])["Delete"].split("token=")[1]
    assert "deleted" in client.post("/moderate", data={"token": token}).text
    assert client.get(f"/comments/{SLUG}").json()["comments"] == []


def test_tampered_or_foreign_token_rejected(client, emails):
    post(client)
    token = links(emails[0])["Approve"].split("token=")[1]
    payload, signature = token.split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    assert client.post("/moderate", data={"token": f"{payload}.{flipped}"}).status_code == 400
    assert client.get("/moderate?token=garbage").status_code == 400


def test_expired_link_rejected(client, emails, monkeypatch):
    post(client)
    token = links(emails[0])["Approve"].split("token=")[1]
    real_time = comments_route.time.time
    monkeypatch.setattr(comments_route.time, "time", lambda: real_time() + 8 * 86400)
    assert client.post("/moderate", data={"token": token}).status_code == 400


def test_admin_moderation(client, emails):
    headers = admin_headers(client)
    post(client, name="First")
    post(client, name="Second")
    assert client.get("/admin/comments").status_code == 401
    pending = client.get("/admin/comments?status=pending", headers=headers).json()["comments"]
    assert [c["name"] for c in pending] == ["Second", "First"]  # newest first
    first_id = pending[1]["id"]
    assert client.post(f"/admin/comments/{first_id}/approve", headers=headers).json()["comment"]["status"] == "approved"
    assert [c["name"] for c in client.get(f"/comments/{SLUG}").json()["comments"]] == ["First"]
    client.post(f"/admin/comments/{first_id}/delete", headers=headers)
    assert client.get(f"/comments/{SLUG}").json()["comments"] == []
    client.post(f"/admin/comments/{first_id}/restore", headers=headers)
    names = [c["name"] for c in client.get("/admin/comments?status=pending", headers=headers).json()["comments"]]
    assert "First" in names


def test_validation_and_spam(client, emails):
    assert post(client, name="").status_code == 422
    assert post(client, text="x" * 2001).status_code == 422
    assert client.post("/comments/Bad Slug!", json={"name": "a", "text": "b"}).status_code == 404
    assert post(client, website="http://spam.example").status_code == 202
    assert emails == []


def test_rate_limit(client, emails):
    for _ in range(5):
        assert post(client, ip="5.5.5.5").status_code == 202
    assert post(client, ip="5.5.5.5").status_code == 429
    assert post(client, ip="6.6.6.6").status_code == 202


def test_comment_saved_even_if_email_fails(client, monkeypatch):
    import httpx

    def fail(**kwargs):
        raise httpx.HTTPError("resend down")

    monkeypatch.setattr(comments_route, "send_email", fail)
    headers = admin_headers(client)
    assert post(client).status_code == 202
    assert len(client.get("/admin/comments", headers=headers).json()["comments"]) == 1


def test_html_is_escaped_in_email(client, emails):
    post(client, name="<script>x</script>", text="<img src=x onerror=alert(1)>")
    assert "<script>" not in emails[0]["html_body"]
    assert "&lt;img" in emails[0]["html_body"]
