"""GitHub API proxy for the admin (Sveltia CMS).

The CMS sends our session token; we check it and forward the request with the
real GITHUB_TOKEN, which never leaves the server. The CMS is configured with
  api_root         = <api>/gh/api/v3   (REST)
  graphql_api_root = <api>/gh/graphql  (GraphQL)
"""

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool

from app import security
from app.config import get_settings

router = APIRouter(tags=["admin github proxy"])

GITHUB_API = "https://api.github.com"
PASS_THROUGH_HEADERS = ("content-type", "etag", "last-modified", "link", "x-ratelimit-remaining", "x-ratelimit-reset")


def _error(status: int, message: str) -> Response:
    return Response(f'{{"message": "{message}"}}', status_code=status, media_type="application/json")


def _target(path: str, query: str) -> str | None:
    if path == "graphql":
        return f"{GITHUB_API}/graphql"
    if not path.startswith("api/v3/"):
        return None
    rest = path.removeprefix("api/v3/")
    repo = get_settings().github_repo
    # Only the signed-in user and the site's own repository are reachable.
    if rest != "user" and rest != f"repos/{repo}" and not rest.startswith(f"repos/{repo}/"):
        return None
    return f"{GITHUB_API}/{rest}" + (f"?{query}" if query else "")


@router.api_route("/gh/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
async def github_proxy(path: str, request: Request):
    auth = request.headers.get("authorization", "")
    session = auth.split(" ", 1)[1] if " " in auth else ""
    # verify_session calls Redis synchronously; keep it off the event loop.
    if not await run_in_threadpool(security.verify_session, session):
        return _error(401, "Bad credentials")

    target = _target(path, request.url.query)
    if not target:
        return _error(404, "Not Found")

    headers = {
        "Accept": request.headers.get("accept", "application/vnd.github+json"),
        "User-Agent": "xamidovasadbek.dev-api",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = get_settings().github_token
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type := request.headers.get("content-type"):
        headers["Content-Type"] = content_type

    async with httpx.AsyncClient(timeout=30) as client:
        upstream = await client.request(request.method, target, headers=headers, content=await request.body())

    response_headers = {"Cache-Control": "no-store"}
    for name in PASS_THROUGH_HEADERS:
        if value := upstream.headers.get(name):
            response_headers[name] = value
    return Response(upstream.content, status_code=upstream.status_code, headers=response_headers)
