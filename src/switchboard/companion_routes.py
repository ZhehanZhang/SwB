from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .blocks.mcp import principal_of
from .companions import DISCONNECTED, CompanionError
from .guard import RateLimiter, audit_client_ip, client_ip

if TYPE_CHECKING:
    from .core import Switchboard

NO_STORE = {"Cache-Control": "no-store"}
COMPANION_ACTIONS = {"hello": "hello", "results": "result", "claims": "claim", "emit": "emit", "revoke": "revoke"}


def _error(exc: CompanionError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=NO_STORE)


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except (ValueError, RecursionError):
        body = None
    if not isinstance(body, dict):
        raise CompanionError("Send a JSON object.")
    return body


def companion_routes(core: Switchboard, limiter: RateLimiter, heartbeat_s: float) -> list[Route]:
    companions = core.companions

    async def mint(request: Request) -> JSONResponse:
        owner, refused = await core.auth.protect_owner(request)
        if refused is not None:
            return refused
        if owner.via != "passkey":
            return _error(CompanionError("Sign in with a passkey.", 403))
        if not (url := core.cfg.issuer):
            return _error(CompanionError("Set a public SwB address first."))
        return JSONResponse(companions.mint(url, origin="http", source=f"admin:{owner.label}", client=audit_client_ip(request.scope)),
                            headers=NO_STORE)

    async def pair(request: Request) -> JSONResponse:
        ip = client_ip(request.scope)
        client = audit_client_ip(request.scope)
        if (wait := limiter.blocked("companion_pair_fail", ip)) is not None:
            core.audit.record_refusal(origin="http", client=client, destination="core", action="companion_pair",
                                      detail="Pairing attempt limit reached.")
            return JSONResponse({"error": "Too many failed pairing attempts."}, status_code=429,
                                headers={**NO_STORE, "Retry-After": str(int(wait + 0.999))})
        try:
            out = companions.pair(await _body(request), client=client)
        except CompanionError as exc:
            limiter.hit("companion_pair_fail", ip)
            core.audit.record_refusal(origin="http", client=client, destination="core", action="companion_pair", detail=str(exc))
            return _error(exc)
        return JSONResponse(out, headers=NO_STORE)

    async def events(request: Request) -> Response:
        companion_id = principal_of(request).client_id.removeprefix("companion:")

        async def frames() -> AsyncIterator[str]:
            async with aclosing(companions.events(companion_id, heartbeat_s)) as stream:
                async for event, data in stream:
                    yield f"event: {event}\ndata: {data}\n\n"

        return StreamingResponse(frames(), media_type="text/event-stream", headers={**NO_STORE, "X-Accel-Buffering": "no"})

    async def up(request: Request) -> JSONResponse:
        principal = principal_of(request)
        companion_id = principal.client_id.removeprefix("companion:")
        verb = request.url.path.rsplit("/", 1)[-1]
        try:
            if verb == "revoke":
                if not companions.revoke(companion_id, origin="companion", source=companion_id, client=principal.client_id):
                    raise CompanionError(DISCONNECTED, 401)
            else:
                body = await _body(request)
                if verb == "hello":
                    companions.hello(companion_id, body, client=audit_client_ip(request.scope))
                elif verb == "results":
                    await companions.result(companion_id, body)
                elif verb == "claims":
                    return JSONResponse(await companions.claim(principal, body), headers=NO_STORE)
                else:
                    return JSONResponse(await companions.emit(principal, body), headers=NO_STORE)
        except CompanionError as exc:
            core.audit.record_refusal(origin="companion", client=audit_client_ip(request.scope), destination="core",
                                      action=f"companion_{COMPANION_ACTIONS[verb]}", detail=str(exc), target=companion_id, ref=exc.audit_ref)
            return _error(exc)
        return JSONResponse({"ok": True}, headers=NO_STORE)

    return [
        Route("/pairing/companion", mint, methods=["POST"]),
        Route("/companion/pair", pair, methods=["POST"]),
        Route("/companion/events", events),
        *(Route(f"/companion/{verb}", up, methods=["POST"]) for verb in COMPANION_ACTIONS),
    ]
