"""Settings read from environment variables (Vercel → Settings → Environment Variables)."""

import os
from dataclasses import dataclass, field


def _list(value: str) -> list[str]:
    # Tolerates quotes and spaces pasted into the Vercel dashboard.
    return [item.strip().strip("\"'").rstrip("/") for item in value.split(",") if item.strip().strip("\"'")]


@dataclass(frozen=True)
class Settings:
    # The only email that can sign in to the admin, and where contact messages go.
    admin_email: str = field(default_factory=lambda: os.getenv("ADMIN_EMAIL", "").strip().lower())
    # Recovery code for setting the first password or resetting a forgotten one.
    admin_setup_code: str = field(default_factory=lambda: os.getenv("ADMIN_SETUP_CODE", ""))
    # GitHub token that can write to the site repo. Never sent to the browser.
    github_token: str = field(default_factory=lambda: os.getenv("GITHUB_TOKEN", ""))
    github_repo: str = field(default_factory=lambda: os.getenv("GITHUB_REPO") or "xamidovasadbekdev-arch/Personal_blog")
    # Upstash Redis (the Vercel integration adds the KV_* names).
    redis_url: str = field(default_factory=lambda: os.getenv("KV_REST_API_URL") or os.getenv("UPSTASH_REDIS_REST_URL", ""))
    redis_token: str = field(default_factory=lambda: os.getenv("KV_REST_API_TOKEN") or os.getenv("UPSTASH_REDIS_REST_TOKEN", ""))
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
