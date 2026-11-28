from __future__ import annotations

from pathlib import Path

from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route, Router
from starlette.staticfiles import StaticFiles
from starlette.types import Message, Receive, Scope, Send

from . import pages
from .api_about import ABOUT_ROUTES
from .api_board import BOARD_ROUTES
from .api_bot_identity import BOT_IDENTITY_ROUTES
from .api_connections import CONNECTION_ROUTES
from .api_memory_backup import MEMORY_BACKUP_ROUTES
from .api_pages import PAGE_ROUTES
from .api_read import READ_ROUTES
from .api_settings import SETTINGS_ROUTES
from .api_tokens import TOKEN_ROUTES
from .api_write import WRITE_ROUTES
from .sessions import login_redirect

APP_DIST = Path(__file__).with_name("static") / "app"
APP_SECTIONS = ("agents", "tokens", "inbox", "memory", "audit", "settings", "about", "connections")
SPA_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; img-src 'self' data:; connect-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow",
}
API_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
CLASSIC_SECTIONS = {
    "tokens": "/tokens", "pairing": "/tokens", "keys": "/tokens",
    "inbox": "/inbox", "requests": "/inbox", "approvals": "/inbox",
    "agents": "/agents", "avatars": "/agents", "personas": "/agents",
}
CLASSIC_NOTICES = {
    "approved": "/inbox", "rejected": "/inbox", "send-failed": "/inbox",
    "decide-refused": "/inbox", "avatar-saved": "/agents", "avatar-refused": "/agents",
}


async def classic_redirect(request: Request) -> Response:
    notice = request.query_params.get("notice", "")
    if (target := CLASSIC_NOTICES.get(notice)) is not None:
        return RedirectResponse(f"{target}?notice={notice}", status_code=308)
    section = request.path_params.get("rest", "").partition("/")[0]
    return RedirectResponse(CLASSIC_SECTIONS.get(section, "/"), status_code=308)


async def overview_redirect(request: Request) -> Response:
    return RedirectResponse("/", status_code=308)


class AppAssets(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        cache = "no-cache" if path == "theme.js" else "public, max-age=31536000, immutable"
        response.headers.update({"Cache-Control": cache, "X-Content-Type-Options": "nosniff"})
        return response


def spa_index() -> FileResponse | None:
    index = APP_DIST / "index.html"
    if index.is_file():
        return FileResponse(index, media_type="text/html", headers=SPA_HEADERS)
    return None


def web_routes() -> list[Route | Mount]:
    async def shell(request: Request) -> Response:
        _, refused = await request.app.state.core.auth.protect_owner(request)
        if refused is not None:
            return login_redirect(request)
        return spa_index() or pages.message_page("Web UI build required", "Run npm run build --prefix web", 503)

    api = Router(routes=[*READ_ROUTES, *WRITE_ROUTES, *BOARD_ROUTES, *SETTINGS_ROUTES, *TOKEN_ROUTES, *MEMORY_BACKUP_ROUTES,
                         *PAGE_ROUTES, *ABOUT_ROUTES, *CONNECTION_ROUTES, *BOT_IDENTITY_ROUTES],
                 redirect_slashes=False)

    async def owner_api(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await api(scope, receive, send)
        request = Request(scope, receive)

        async def no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.update(API_HEADERS)
            await send(message)

        try:
            owner, refused = await request.app.state.core.auth.protect_owner(request)
            if refused is not None:
                return await refused(scope, receive, no_store)
            request.state.owner = owner
            await api(scope, receive, no_store)
        except HTTPException as exc:
            response = JSONResponse({"error": {404: "not_found", 405: "method_not_allowed", 413: "request_too_large"}.get(exc.status_code, "request_failed")},
                                    status_code=exc.status_code, headers=exc.headers)
            await response(scope, receive, no_store)
        finally:
            await request.close()

    return [
        *([Mount("/app-assets/assets", app=AppAssets(directory=APP_DIST / "assets"))] if (APP_DIST / "assets").is_dir() else []),
        Mount("/api", app=owner_api),
        Route("/classic", classic_redirect),
        Route("/classic/{rest:path}", classic_redirect),
        Route("/canvas", overview_redirect),
        Route("/canvas/{rest:path}", overview_redirect),
        Route("/projects", overview_redirect),
        Route("/projects/", overview_redirect),
        Route("/projects/{rest:path}", shell),
        Route("/", shell),
        *(Route(f"/{section}", shell) for section in APP_SECTIONS),
        *(Route(f"/{section}/{{rest:path}}", shell) for section in APP_SECTIONS),
    ]
