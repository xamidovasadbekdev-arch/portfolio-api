"""Contact form: validates the message and emails it to the site owner via Resend."""

import html

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from app import security
from app.config import get_settings
from app.deps import client_ip

router = APIRouter(tags=["contact"])

# 5 messages per hour from one address, 100 per hour overall.
CONTACT_LIMIT = {"per_ip": 5, "total": 100}
WINDOW_SECONDS = 3600


class ContactBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    subject: str = Field(default="", max_length=150)
    message: str = Field(min_length=1, max_length=5000)
    # Hidden form field; people leave it empty, bots fill it in.
    website: str = Field(default="", max_length=200)


def send_email(*, subject: str, text: str, html_body: str, reply_to: str) -> None:
    """Send one email through the Resend API. Raises on failure."""
    settings = get_settings()
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        json={
            "from": settings.contact_from,
            "to": [settings.admin_email],
            "reply_to": reply_to,
            "subject": subject,
            "text": text,
            "html": html_body,
        },
        timeout=15,
    )
    response.raise_for_status()


@router.post("/contact")
def contact(body: ContactBody, request: Request):
    """Receive a contact-form message and forward it to the owner's inbox."""
    settings = get_settings()
    if not settings.resend_api_key or not settings.admin_email:
        raise HTTPException(503, "The contact form is not configured yet.")

    if body.website:
        # Pretend it worked so bots don't retry.
        return {"ok": True}

    ip = client_ip(request)
    if security.is_limited("contact", ip, **CONTACT_LIMIT):
        raise HTTPException(429, "Too many messages. Please try again later or email directly.")
    security.count_attempt("contact", ip, WINDOW_SECONDS)

    name = " ".join(body.name.split())
    subject = " ".join(body.subject.split()) or f"New message from {name}"
    text = f"From: {name} <{body.email}>\n\n{body.message}\n\n— sent from the contact form on xamidovasadbek.dev"
    html_body = (
        f"<p><strong>From:</strong> {html.escape(name)} &lt;{html.escape(body.email)}&gt;</p>"
        f"<p style='white-space:pre-wrap'>{html.escape(body.message)}</p>"
        "<p style='color:#888'>Sent from the contact form on xamidovasadbek.dev. Reply to this email to answer.</p>"
    )
    try:
        send_email(subject=f"[xamidov.dev] {subject}", text=text, html_body=html_body, reply_to=str(body.email))
    except httpx.HTTPError:
        raise HTTPException(502, "The message could not be sent. Please email directly.")
    return {"ok": True}
