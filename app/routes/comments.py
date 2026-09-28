"""Article comments with owner approval.

Flow: a reader posts a comment → it is stored as "pending" and the owner gets
an email with Approve / Delete buttons → approved comments show under the
article. The owner can also moderate from /admin/comments on the site.

Storage (Redis):
  comment:<id>                  hash  {id, slug, name, text, created, status}
  comments:pending              list  of ids waiting for approval
  comments:approved:<slug>      list  of approved ids for one article, oldest first
  comments:all                  list  of every id, oldest first (admin view)
  comments:secret               key   signs the email approve/delete links (GET/POST /moderate)
"""

import base64
import hashlib
import hmac
import html
import json
import re
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app import security
from app.config import get_settings
from app.deps import client_ip
from app.email import send_email
from app.store import get_store

router = APIRouter(tags=["comments"])

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,150}$")
COMMENT_LIMIT = {"per_ip": 5, "total": 200}
WINDOW_SECONDS = 3600
LINK_DAYS = 7
SITE_URL = "https://xamidovasadbek.dev"


class CommentBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    text: str = Field(min_length=1, max_length=2000)
    # Hidden form field; people leave it empty, bots fill it in.
    website: str = Field(default="", max_length=200)


# ---- Storage helpers -----------------------------------------------------------

def _text(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _load(comment_id: str) -> dict | None:
    data = get_store().hgetall(f"comment:{comment_id}") or {}
    data = {_text(k): _text(v) for k, v in data.items()}
    return data if data.get("id") else None


def _ids(key: str) -> list[str]:
    return [_text(i) for i in (get_store().lrange(key, 0, -1) or [])]


def _public(comment: dict) -> dict:
    return {"id": comment["id"], "name": comment["name"], "text": comment["text"], "created": comment["created"]}


def _check_slug(slug: str) -> str:
    if not SLUG.match(slug):
        raise HTTPException(404, "Unknown article.")
    return slug


def _set_status(comment: dict, status: str) -> dict:
    store = get_store()
    comment_id, slug = comment["id"], comment["slug"]
    if comment["status"] == status:
        return comment
    store.lrem("comments:pending", 0, comment_id)
    store.lrem(f"comments:approved:{slug}", 0, comment_id)
    if status == "approved":
        store.rpush(f"comments:approved:{slug}", comment_id)
    elif status == "pending":
        store.rpush("comments:pending", comment_id)
    store.hset(f"comment:{comment_id}", values={"status": status})
    return {**comment, "status": status}


# ---- Signed links for the email buttons ------------------------------------------

def _link_secret() -> str:
    store = get_store()
    store.set("comments:secret", f"s_{secrets.token_hex(32)}", nx=True)
    return _text(store.get("comments:secret"))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_link_token(comment_id: str, action: str) -> str:
    payload = _b64(json.dumps({"id": comment_id, "a": action, "exp": int(time.time()) + LINK_DAYS * 86400}).encode())
    signature = _b64(hmac.new(_link_secret().encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def read_link_token(token: str) -> tuple[str, str] | None:
    try:
        payload, signature = token.split(".")
        expected = _b64(hmac.new(_link_secret().encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return None
    if data.get("exp", 0) < time.time() or data.get("a") not in ("approve", "delete"):
        return None
    return data["id"], data["a"]


def _notify_owner(comment: dict, api_base: str) -> None:
    approve = f"{api_base}/moderate?token={make_link_token(comment['id'], 'approve')}"
    delete = f"{api_base}/moderate?token={make_link_token(comment['id'], 'delete')}"
    article = f"{SITE_URL}/blog/{comment['slug']}"
    name, text = html.escape(comment["name"]), html.escape(comment["text"])
    button = "display:inline-block;padding:10px 18px;border-radius:999px;text-decoration:none;font-weight:600;margin-right:8px"
    send_email(
        subject=f"[xamidov.dev] New comment from {comment['name']}",
        text=(
            f"{comment['name']} commented on {article}:\n\n{comment['text']}\n\n"
            f"Approve: {approve}\nDelete: {delete}\n\n(Links work for {LINK_DAYS} days. "
            f"You can also moderate at {SITE_URL}/admin/comments)"
        ),
        html_body=(
            f"<p><strong>{name}</strong> commented on <a href='{article}'>{html.escape(comment['slug'])}</a>:</p>"
            f"<blockquote style='white-space:pre-wrap;border-left:3px solid #e4ac48;margin:0;padding:4px 12px'>{text}</blockquote>"
            f"<p style='margin-top:20px'>"
            f"<a href='{approve}' style='{button};background:#1f7a3a;color:#fff'>Approve</a>"
            f"<a href='{delete}' style='{button};background:#eee;color:#333'>Delete</a></p>"
            f"<p style='color:#888;font-size:12px'>Buttons work for {LINK_DAYS} days. You can also moderate at "
            f"<a href='{SITE_URL}/admin/comments'>{SITE_URL}/admin/comments</a>.</p>"
        ),
    )


# ---- Public endpoints --------------------------------------------------------------

@router.get("/comments/{slug}")
def list_comments(slug: str):
    """Approved comments for one article, oldest first."""
    _check_slug(slug)
    comments = [_load(i) for i in _ids(f"comments:approved:{slug}")]
    return {"comments": [_public(c) for c in comments if c and c["status"] == "approved"]}


@router.post("/comments/{slug}", status_code=202)
def add_comment(slug: str, body: CommentBody, request: Request):
    """Store a new comment as pending and email the owner."""
    _check_slug(slug)
    if body.website:
        return {"status": "pending"}  # bot; pretend it worked

    ip = client_ip(request)
    if security.is_limited("comment", ip, **COMMENT_LIMIT):
        raise HTTPException(429, "Too many comments. Please try again later.")
    security.count_attempt("comment", ip, WINDOW_SECONDS)

    comment = {
        "id": secrets.token_hex(8),
        "slug": slug,
        "name": " ".join(body.name.split()),
        "text": body.text.strip(),
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "pending",
    }
    store = get_store()
    store.hset(f"comment:{comment['id']}", values=comment)
    store.rpush("comments:pending", comment["id"])
    store.rpush("comments:all", comment["id"])

    settings = get_settings()
    if settings.resend_api_key and settings.admin_email:
        api_base = str(request.base_url).rstrip("/")
        try:
            _notify_owner(comment, api_base)
        except httpx.HTTPError:
            pass  # the comment is saved; it can still be approved from /admin/comments
    return {"status": "pending"}


# ---- Email buttons ---------------------------------------------------------------------
# GET only shows a confirmation page: email apps and link scanners open links
# on their own, so the actual change happens on the POST from that page.

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>Comment · xamidov.dev</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#14120f;color:#c4baa9;
font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;padding:24px}}main{{max-width:520px;width:100%}}
h1{{color:#f2ebdf;font-size:22px;font-weight:600}}blockquote{{white-space:pre-wrap;border-left:3px solid #e4ac48;
margin:16px 0;padding:4px 14px;color:#f2ebdf}}button{{padding:11px 22px;border:0;border-radius:999px;font:inherit;
font-weight:600;cursor:pointer;background:#f2ebdf;color:#14120f}}a{{color:#e4ac48}}</style></head>
<body><main>{body}</main></body></html>"""


def _page(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(PAGE.format(body=body), status_code=status, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})


@router.get("/moderate", response_class=HTMLResponse, include_in_schema=False)
def moderate_page(token: str = ""):
    parsed = read_link_token(token)
    comment = _load(parsed[0]) if parsed else None
    if not comment:
        return _page("<h1>This link has expired or is invalid.</h1>"
                     f"<p>Moderate comments at <a href='{SITE_URL}/admin/comments'>/admin/comments</a>.</p>", 400)
    action = parsed[1]
    label = "Approve" if action == "approve" else "Delete"
    return _page(
        f"<h1>{label} this comment?</h1>"
        f"<p><strong>{html.escape(comment['name'])}</strong> on <a href='{SITE_URL}/blog/{comment['slug']}'>"
        f"{html.escape(comment['slug'])}</a> · currently <em>{comment['status']}</em></p>"
        f"<blockquote>{html.escape(comment['text'])}</blockquote>"
        f"<form method='post'><input type='hidden' name='token' value='{html.escape(token)}'>"
        f"<button>{label}</button></form>"
    )


@router.post("/moderate", response_class=HTMLResponse, include_in_schema=False)
async def moderate_submit(request: Request):
    # Plain form post from the confirmation page (parsed here to avoid an extra dependency).
    token = parse_qs((await request.body()).decode()).get("token", [""])[0]
    parsed = await run_in_threadpool(read_link_token, token)
    comment = await run_in_threadpool(_load, parsed[0]) if parsed else None
    if not comment:
        return _page("<h1>This link has expired or is invalid.</h1>", 400)
    status = "approved" if parsed[1] == "approve" else "deleted"
    await run_in_threadpool(_set_status, comment, status)
    done = "approved — it is now visible under the article" if status == "approved" else "deleted"
    return _page(f"<h1>Comment {done}.</h1><p><a href='{SITE_URL}/blog/{comment['slug']}'>Open the article</a> · "
                 f"<a href='{SITE_URL}/admin/comments'>All comments</a></p>")


# ---- Admin (signed-in owner) ---------------------------------------------------------------

async def require_admin(request: Request) -> None:
    auth = request.headers.get("authorization", "")
    session = auth.split(" ", 1)[1] if " " in auth else ""
    if not await run_in_threadpool(security.verify_session, session):
        raise HTTPException(401, "Please sign in again.")


@router.get("/admin/comments", dependencies=[Depends(require_admin)])
def admin_list(status: str = "pending"):
    """All comments with the given status, newest first."""
    if status not in ("pending", "approved", "deleted", "all"):
        raise HTTPException(400, "Unknown status.")
    comments = [c for c in (_load(i) for i in reversed(_ids("comments:all"))) if c]
    if status != "all":
        comments = [c for c in comments if c["status"] == status]
    return {"comments": comments}


@router.post("/admin/comments/{comment_id}/{action}", dependencies=[Depends(require_admin)])
def admin_moderate(comment_id: str, action: str):
    if action not in ("approve", "delete", "restore"):
        raise HTTPException(400, "Unknown action.")
    comment = _load(comment_id)
    if not comment:
        raise HTTPException(404, "Comment not found.")
    status = {"approve": "approved", "delete": "deleted", "restore": "pending"}[action]
    return {"comment": _set_status(comment, status)}
