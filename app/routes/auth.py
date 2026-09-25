"""Admin sign-in: the login window, password checks, password changes."""

import hmac
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app import security
from app.config import get_settings
from app.deps import client_ip

router = APIRouter(tags=["admin auth"])

LOGIN_LIMIT = {"per_ip": 5, "total": 30}
LOCK_SECONDS = 15 * 60


class LoginBody(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


class PasswordChangeBody(BaseModel):
    email: str = Field(max_length=200)
    current: str = Field(max_length=200)
    next: str = Field(min_length=security.MIN_PASSWORD_LENGTH, max_length=72)


class SetupBody(BaseModel):
    email: str = Field(max_length=200)
    code: str = Field(max_length=500)
    password: str = Field(min_length=security.MIN_PASSWORD_LENGTH, max_length=72)


def _guard(request: Request) -> str:
    ip = client_ip(request)
    if security.is_limited("login", ip, **LOGIN_LIMIT):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    return ip


def _fail(ip: str, message: str):
    security.count_attempt("login", ip, LOCK_SECONDS)
    raise HTTPException(401, message)


@router.post("/login")
def login(body: LoginBody, request: Request):
    """Check email + password and return a 12-hour admin session."""
    ip = _guard(request)
    account = security.get_account()
    # Always run bcrypt so every failure takes the same time.
    password_ok = security.check_password(body.password, account["hash"] if account else None)
    if not account:
        raise HTTPException(409, "No password has been set yet. Open /admin/account to set one.")
    if not (security.emails_match(body.email, get_settings().admin_email) and password_ok):
        _fail(ip, "Wrong email or password.")
    security.reset_attempts("login", ip)
    return {"token": security.create_session(account)}


@router.post("/password")
def change_password(body: PasswordChangeBody, request: Request):
    """Change the password. Signs out every existing session."""
    ip = _guard(request)
    account = security.get_account()
    if not account:
        raise HTTPException(409, "No password has been set yet.")
    password_ok = security.check_password(body.current, account["hash"])
    if not (security.emails_match(body.email, get_settings().admin_email) and password_ok):
        _fail(ip, "Wrong email or current password.")
    security.save_password(body.next)
    security.reset_attempts("login", ip)
    return {"ok": True}


@router.post("/setup")
def setup_password(body: SetupBody, request: Request):
    """Set the first password, or reset a forgotten one, with the recovery code."""
    settings = get_settings()
    if not settings.admin_setup_code:
        raise HTTPException(403, "Setup is turned off (ADMIN_SETUP_CODE is not set).")
    ip = _guard(request)
    code_ok = hmac.compare_digest(body.code.encode(), settings.admin_setup_code.encode())
    if not (security.emails_match(body.email, settings.admin_email) and code_ok):
        _fail(ip, "Wrong email or recovery code.")
    security.save_password(body.password)
    security.reset_attempts("login", ip)
    return {"ok": True}


@router.get("/auth", response_class=HTMLResponse, include_in_schema=False)
def auth_window():
    """The sign-in window the CMS opens. It hands the session to the CMS with
    the standard Decap/Sveltia popup handshake:
      window -> CMS: "authorizing:github"
      CMS -> window: "authorizing:github"
      window -> CMS: 'authorization:github:success:{"token": ..., "provider": "github"}'
    """
    html = AUTH_PAGE.replace("__ALLOWED_ORIGINS__", json.dumps(get_settings().allowed_origins))
    return HTMLResponse(
        html,
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"},
    )


AUTH_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Sign in · xamidov.dev</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #14120f; color: #c4baa9;
         font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; padding: 24px; }
  form { width: 100%; max-width: 340px; display: grid; gap: 14px; }
  h1 { margin: 0 0 4px; color: #f2ebdf; font-size: 22px; font-weight: 600; }
  p { margin: 0; font-size: 13px; color: #8b8272; }
  label { display: grid; gap: 6px; font-size: 13px; color: #f2ebdf; }
  input { width: 100%; padding: 11px 13px; border-radius: 10px; border: 1px solid rgba(240,228,206,.2);
          background: #1b1814; color: #f2ebdf; font: inherit; outline: none; }
  input:focus { border-color: #e4ac48; }
  button { margin-top: 6px; padding: 11px; border: 0; border-radius: 999px; background: #f2ebdf; color: #14120f;
           font: inherit; font-weight: 600; cursor: pointer; }
  button:disabled { opacity: .6; cursor: default; }
  .error { color: #f0a58a; min-height: 20px; font-size: 13px; }
</style>
</head>
<body>
<form id="form">
  <h1>Admin sign in</h1>
  <p>xamidov.dev</p>
  <label>Email <input name="email" type="email" autocomplete="username" required autofocus></label>
  <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
  <div class="error" id="error" role="alert"></div>
  <button id="submit">Sign in</button>
</form>
<script>
  const ALLOWED_ORIGINS = __ALLOWED_ORIGINS__;
  const provider = 'github';
  const form = document.getElementById('form');
  const errorBox = document.getElementById('error');
  const button = document.getElementById('submit');

  function handOver(token) {
    const message = 'authorization:' + provider + ':success:' + JSON.stringify({ token, provider });
    window.addEventListener('message', function onMessage(event) {
      // Only answer the admin page of our own site.
      if (!ALLOWED_ORIGINS.includes(event.origin) || event.data !== 'authorizing:' + provider) return;
      window.removeEventListener('message', onMessage);
      window.opener.postMessage(message, event.origin);
    });
    // The opener is one of our sites; a message aimed at the wrong origin is simply dropped.
    for (const origin of ALLOWED_ORIGINS) window.opener.postMessage('authorizing:' + provider, origin);
  }

  function describe(detail) {
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail[0] && detail[0].msg) return detail[0].msg;
    return 'Sign-in failed.';
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    errorBox.textContent = '';
    if (!window.opener) {
      errorBox.textContent = 'Open this from the admin page and click Sign In.';
      return;
    }
    button.disabled = true;
    button.textContent = 'Signing in…';
    try {
      const response = await fetch('login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: form.email.value, password: form.password.value }),
      });
      const result = await response.json();
      if (!response.ok || !result.token) throw new Error(describe(result.detail));
      button.textContent = 'Opening admin…';
      handOver(result.token);
    } catch (error) {
      errorBox.textContent = error.message || 'Sign-in failed.';
      button.disabled = false;
      button.textContent = 'Sign in';
    }
  });
</script>
</body>
</html>
"""
