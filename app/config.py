"""Settings read from environment variables (Vercel → Settings → Environment Variables)."""

import os
from dataclasses import dataclass, field


def _list(value: str) -> list[str]:
    # Tolerates quotes and spaces pasted into the Vercel dashboard.
    return [item.strip().strip("\"'").rstrip("/") for item in value.split(",") if item.strip().strip("\"'")]


def _redis_credentials() -> tuple[str, str]:
    """Find the Upstash REST URL and token.

    The Vercel integration names them <PREFIX>_REST_API_URL / <PREFIX>_REST_API_TOKEN,
    where the prefix is chosen when connecting (KV by default, sometimes STORAGE).
    """
    for url_name, token_name in (
        ("KV_REST_API_URL", "KV_REST_API_TOKEN"),
        ("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN"),
    ):
        if os.getenv(url_name) and os.getenv(token_name):
            return os.environ[url_name], os.environ[token_name]
    for name, url in os.environ.items():
        if name.endswith("_REST_API_URL") and url:
            token = os.getenv(name.removesuffix("_URL") + "_TOKEN")
            if token:
                return url, token
    return "", ""


@dataclass(frozen=True)
class Settings:
    # The only email that can sign in to the admin, and where contact messages go.
    admin_email: str = field(default_factory=lambda: os.getenv("ADMIN_EMAIL", "").strip().lower())
    # Recovery code for setting the first password or resetting a forgotten one.
    admin_setup_code: str = field(default_factory=lambda: os.getenv("ADMIN_SETUP_CODE", ""))
    # GitHub token that can write to the site repo. Never sent to the browser.
    github_token: str = field(default_factory=lambda: os.getenv("GITHUB_TOKEN", ""))
    github_repo: str = field(default_factory=lambda: os.getenv("GITHUB_REPO") or "xamidovasadbekdev-arch/Personal_blog")
    # Upstash Redis, added by the Vercel integration.
    redis_url: str = field(default_factory=lambda: _redis_credentials()[0])
    redis_token: str = field(default_factory=lambda: _redis_credentials()[1])
    # Resend (https://resend.com) for contact-form emails.
    resend_api_key: str = field(default_factory=lambda: os.getenv("RESEND_API_KEY", ""))
    # Until the domain is verified in Resend, onboarding@resend.dev can only
    # send to the Resend account's own email address.
    contact_from: str = field(default_factory=lambda: os.getenv("CONTACT_FROM") or "Portfolio <onboarding@resend.dev>")
    # Sites allowed to call this API from the browser.
    allowed_origins: list[str] = field(
        default_factory=lambda: _list(
            os.getenv("ALLOWED_ORIGINS")
            or "https://xamidovasadbek.dev,https://www.xamidovasadbek.dev,http://localhost:5173"
        )
    )


def get_settings() -> Settings:
    return Settings()
