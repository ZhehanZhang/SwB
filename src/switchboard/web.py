from __future__ import annotations

import hmac
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route, Router
from starlette.staticfiles import StaticFiles
from starlette.types import Message, Receive, Scope, Send

from . import pages
from .api_read import READ_ROUTES
from .api_write import WRITE_ROUTES

APP_DIST = Path(__file__).with_name("static") / "app"
APP_SECTIONS = ("canvas", "agents", "tokens", "inbox", "provision", "projects", "memory", "audit", "settings")
SPA_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; img-src 'self' data:; connect-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow",
}
API_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


class AppAssets(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers.update({"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})
        return response


def _origin(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname
    if not host or parts.scheme not in ("http", "https"):
        return ""
    port = parts.port
    if ":" in host:
        host = f"[{host}]"
    authority = f"{host}:{port}" if port is not None and port != (443 if parts.scheme == "https" else 80) else host
    return f"{parts.scheme}://{authority}"


def web_routes(owner_page: Callable[[Request], Awaitable[tuple[str | None, Response | None]]],
               csrf: Callable[[str], str]) -> list[Route | Mount]:
    async def login(request: Request) -> Response:
        index = APP_DIST / "index.html"
        if not index.is_file():
            return pages.message_page("Web UI not built", "Use /classic or build the web UI first.", 503)
        return FileResponse(index, media_type="text/html", headers=SPA_HEADERS)

    async def shell(request: Request) -> Response:
        _, refused = await owner_page(request)
        return refused if refused is not None else await login(request)

    async def session(request: Request) -> Response:
        email, _ = await owner_page(request)
        return JSONResponse({
            "signed_in": email is not None,
            "via": "access" if email else None,
            "csrf": csrf(email) if email else None,
            "methods": {"passkeys": 0, "oidc": None, "access": bool(request.app.state.core.cfg.access_team_domain)},
        }, headers=API_HEADERS)

    api = Router(routes=[*READ_ROUTES, *WRITE_ROUTES], redirect_slashes=False)

    async def owner_api(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await api(scope, receive, send)
        request = Request(scope, receive)

        async def no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.update(API_HEADERS)
            await send(message)

        email, _ = await owner_page(request)
        if email is None:
            response = JSONResponse({"error": "signed_out"}, status_code=401)
            return await response(scope, receive, no_store)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            core = request.app.state.core
            try:
                expected_origin = _origin(core.cfg.public_url or str(request.url))
            except ValueError:
                expected_origin = ""
            valid_origin = bool(expected_origin) and request.headers.getlist("origin") == [expected_origin]
            provided = request.headers.getlist("x-csrf-token")
            if (not valid_origin or len(provided) != 1
                    or not hmac.compare_digest(provided[0].encode(), csrf(email).encode())):
                response = JSONResponse({"error": "csrf"}, status_code=403)
                return await response(scope, receive, no_store)
        request.state.owner_email = email
        try:
            await api(scope, receive, no_store)
        except HTTPException as exc:
            response = JSONResponse({"error": {404: "not_found", 405: "method_not_allowed", 413: "request_too_large"}.get(exc.status_code, "request_failed")},
                                    status_code=exc.status_code, headers=exc.headers)
            await response(scope, receive, no_store)

    return [
        *([Mount("/app-assets/assets", app=AppAssets(directory=APP_DIST / "assets"))] if (APP_DIST / "assets").is_dir() else []),
        Mount("/api", app=owner_api),
        Route("/auth/session", session),
        Route("/login", login),
        Route("/", shell),
        *(Route(f"/{section}", shell) for section in APP_SECTIONS),
        *(Route(f"/{section}/{{rest:path}}", shell) for section in APP_SECTIONS),
    ]
