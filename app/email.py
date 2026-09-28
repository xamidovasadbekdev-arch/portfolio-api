"""Sending email through Resend (https://resend.com)."""

import httpx

from app.config import get_settings


def send_email(*, subject: str, text: str, html_body: str, reply_to: str | None = None) -> None:
    """Send one email to the site owner. Raises httpx.HTTPError on failure."""
    settings = get_settings()
    payload = {
        "from": settings.contact_from,
        "to": [settings.admin_email],
        "subject": subject,
        "text": text,
        "html": html_body,
    }
    if reply_to:
        payload["reply_to"] = reply_to
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        json=payload,
        timeout=15,
    )
    response.raise_for_status()
