"""portfolio-api — backend for xamidovasadbek.dev.

- Admin sign-in (email + password) and the GitHub proxy the admin panel saves through
- Contact form that emails messages to the site owner
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routes import auth, contact, github

app = FastAPI(
    title="xamidovasadbek.dev API",
    description="Backend for the portfolio site: admin sign-in, content saving through GitHub, and the contact form.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
    expose_headers=["ETag", "Link", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
    max_age=3600,
)

app.include_router(auth.router)
app.include_router(github.router)
app.include_router(contact.router)


@app.get("/", tags=["health"])
def health():
    """Health check. `configured` says which settings are present (never their values)."""
    settings = get_settings()
    return {
        "service": "xamidovasadbek.dev API",
        "status": "ok",
        "configured": {
            "database": bool(settings.redis_url and settings.redis_token),
            "admin_email": bool(settings.admin_email),
            "recovery_code": bool(settings.admin_setup_code),
            "github_token": bool(settings.github_token),
            "email_sending": bool(settings.resend_api_key),
        },
    }
