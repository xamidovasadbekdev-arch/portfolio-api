# portfolio-api

FastAPI backend for [xamidovasadbek.dev](https://xamidovasadbek.dev), live at **https://api.xamidovasadbek.dev** (interactive docs at `/docs`).

The frontend ([Personal_blog](https://github.com/xamidovasadbekdev-arch/Personal_blog)) is a static React site. This service adds the parts that need a server:

| Endpoint | What it does |
|---|---|
| `POST /contact` | Validates a contact-form message and emails it to the owner through [Resend](https://resend.com). Honeypot field, 5 messages/hour per IP. |
| `GET /auth` | The sign-in window the admin panel opens (email + password). |
| `POST /login` | Checks email + password (bcrypt) and returns a signed 12-hour session. |
| `POST /password` | Changes the password; signs out every existing session. |
| `POST /setup` | Sets the first password, or resets a forgotten one, with a recovery code. |
| `* /gh/{path}` | GitHub API proxy for the admin panel (Sveltia CMS). Checks the session and adds the real GitHub token on the server, so the token never reaches a browser. Only the site repository is reachable. |
| `GET /` | Health check. |

## How the admin panel signs in

```
xamidovasadbek.dev/admin  ──"Sign In"──▶  api…/auth (email + password)
        ▲                                       │ POST /login → session
        └──────── session (postMessage) ◀───────┘
admin saves an article ──▶ api…/gh/…  ──(checks session, adds GITHUB_TOKEN)──▶ api.github.com
                                                          │
                                        commit on main ──▶ Vercel rebuilds the site
```

Security: bcrypt password hash in Upstash Redis (never the password itself); sessions signed with HMAC and invalidated on password change; 5 failed attempts per IP (30 overall) lock sign-in for 15 minutes; CORS limited to the site's own addresses; the sign-in window only hands the session to those addresses.

## Stack

FastAPI · Pydantic · httpx · bcrypt · Upstash Redis · Resend · Vercel (Python functions) · pytest

## Run locally

```bash
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt uvicorn pytest
copy .env.example .env      # fill in what you need; without Upstash, data is kept in memory
.venv\Scripts\python -m uvicorn app.main:app --reload --env-file .env
```

Open http://localhost:8000/docs. The frontend's dev server (`npm run dev`) talks to `http://localhost:8000` automatically.

## Tests

```bash
.venv\Scripts\python -m pytest
```

## Deploy (Vercel)

`vercel.json` sends every request to `api/index.py`, which serves the FastAPI app. Environment variables (Settings → Environment Variables):

| Name | Value |
|---|---|
| `ADMIN_EMAIL` | The only email that can sign in; contact messages are sent here |
| `ADMIN_SETUP_CODE` | A long recovery code you make up |
| `GITHUB_TOKEN` | GitHub token with write access to the site repo (classic, `repo` scope) |
| `RESEND_API_KEY` | From resend.com → API Keys |
| `CONTACT_FROM` | Optional. `Portfolio <contact@xamidovasadbek.dev>` once the domain is verified in Resend |
| `KV_REST_API_URL`, `KV_REST_API_TOKEN` | Added automatically by the Upstash integration |
