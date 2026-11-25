from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from . import pages
from .mcp_server import PRINCIPAL_SCOPE, build_mcp

if TYPE_CHECKING:
    from .core import Bridge


class BearerGate:
    """Requires a valid OAuth access token or static key on /mcp; puts the principal on the ASGI scope."""

    def __init__(self, app: Any, bridge: "Bridge"):
        self.app = app
        self.bridge = bridge

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and (scope["path"] == "/mcp" or scope["path"].startswith("/mcp/")):
            scope.pop(PRINCIPAL_SCOPE, None)
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            auth = headers.get("authorization", "")
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
            principal = self.bridge.auth.verify_bearer(token)
            if principal is None:
                challenge = self.bridge.auth.challenge_header("invalid_token" if token else None)
                body = json.dumps({"error": "invalid_token" if token else "unauthorized", "error_description": "sign in through OAuth or send a valid bearer key"}).encode()
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json"), (b"www-authenticate", challenge.encode())]})
                await send({"type": "http.response.body", "body": body})
                return
            scope[PRINCIPAL_SCOPE] = principal
        await self.app(scope, receive, send)


def create_app(bridge: "Bridge") -> Any:
    mcp = build_mcp(bridge)
    mcp_app = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse(bridge.health())

    async def root(request: Request) -> Response:
        return pages.status_page(bridge.health())

    @asynccontextmanager
    async def lifespan(app: Starlette):  # type: ignore[no-untyped-def]
        async with mcp_app.router.lifespan_context(mcp_app):
            yield
        await bridge.aclose()

    app = Starlette(
        routes=[Route("/", root), Route("/healthz", healthz), *pages.routes(), *bridge.auth.routes(), Mount("/", app=mcp_app)],
        lifespan=lifespan,
    )
    return BearerGate(app, bridge)
