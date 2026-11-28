from __future__ import annotations

import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

import uvicorn
import yaml
from starlette.responses import RedirectResponse
from starlette.routing import Route

from switchboard import credentials, pages, web
from switchboard.app import create_app
from switchboard.claims import CLAIM_TTL_S, Field
from switchboard.core import Switchboard
from switchboard.sessions import Owner

PORT = int(os.environ.get("SWB_SERVER_PAGES_PORT", "4184"))
ORIGIN = f"http://127.0.0.1:{PORT}"
ACCOUNT = "owner@example.test"
MUSE_CALLBACK = "https://agent.meta.ai/api/hatch/oauth/callback"
UNKNOWN_CALLBACK = "https://app.example.test/oauth/callback"
MAILBOX = "northwind-procurement-assistant-staging-environment-" + "x" * 40
CSRF = "fixture-csrf"
CLOCK = {"host": "swb.example.test", "now": 0, "expires_at": 600}


async def verify_access(assertion: str) -> str | None:
    return ACCOUNT if assertion == "server-pages-fixture" else None


def run(path: Path) -> None:
    config = path / "switchboard.yaml"
    config.write_text(yaml.safe_dump({
        "version": 2,
        "public_url": ORIGIN,
        "auth": {
            "owner_emails": [ACCOUNT],
            "cloudflare_access": {"team_domain": "access.example.test", "aud": "server-pages-fixture"},
            "redirect_uris": [MUSE_CALLBACK, UNKNOWN_CALLBACK],
            "source_names": {"app.example.test": MAILBOX},
        },
        "blocks": {"mcp": {"use": "mcp"}},
    }))
    now = 1764300000
    core = Switchboard(config, path / "data", env={}, access_verifier=verify_access, clock=lambda: now)
    expired_link = core.claims.mint("EXAMPLE_EXPIRED_TOKEN", ORIGIN).removeprefix(ORIGIN)
    now += CLAIM_TTL_S + 1
    muse = asyncio.run(core.auth.register({"client_name": "Muse", "redirect_uris": [MUSE_CALLBACK]}))
    unknown = asyncio.run(core.auth.register({"client_name": "Sample Studio", "redirect_uris": [UNKNOWN_CALLBACK]}))
    web.APP_DIST = path / "empty-app"
    app = create_app(core)

    def authorize(client: dict, callback: str, scope: str = "switchboard") -> RedirectResponse:
        return RedirectResponse("/authorize?" + urlencode({
            "response_type": "code", "client_id": client["client_id"], "redirect_uri": callback,
            "code_challenge": "A" * 43, "code_challenge_method": "S256", "scope": scope, "state": "fixture-state",
        }))

    async def consent(request):
        return authorize(muse, MUSE_CALLBACK)

    async def consent_unknown(request):
        return authorize(unknown, UNKNOWN_CALLBACK, "switchboard memory.read")

    async def oauth_error(request):
        return RedirectResponse("/authorize?client_id=fixture-missing")

    async def sign_in(request):
        return pages.login_page({"passkeys": False, "oidc": {"name": "Example"}}, "/", None)

    async def passkey(request):
        return pages.login_page({"passkeys": True, "oidc": None}, "/", None)

    async def passkey_failed(request):
        return pages.login_page({"passkeys": True, "oidc": None}, "/", "passkey_failed")

    async def setup(request):
        return pages.login_page({"passkeys": False, "oidc": None, "setup": True}, "/", None)

    async def pairing(request):
        return pages.connect_code_page(
            {"code": "example-pairing-code", "agent": "claude-code", "project": "studio"},
            "https://swb.example.test",
        )

    async def pairing_error(request):
        return pages.message_page("Pairing code unavailable", "", 400)

    async def token(request):
        return pages.token_claim_page("DISCORD_BOT_TOKEN", CSRF, **CLOCK)

    async def routine(request):
        return pages.routine_claim_page({
            "project": "studio", "title": "Morning review", "routine_id": "trig_example",
            "created_by": "agent:claude", "expires_at": 600,
        }, CSRF, host=CLOCK["host"], now=CLOCK["now"])

    async def group(request):
        return credentials.page({
            "title": "GitHub App", "provider": "github", "expires_at": 600,
            "fields": [Field("EXAMPLE_APP_ID", "App ID", "id"), Field("EXAMPLE_PRIVATE_KEY", "Private key", "pem")],
        }, CSRF, host=CLOCK["host"], now=CLOCK["now"])

    async def saved(request):
        return pages.claim_message_page("Token saved", 200)

    async def expired(request):
        return RedirectResponse(expired_link)

    async def owner_key(request):
        return pages.claim_page("Save a token", "", CSRF, owner_key=True)

    async def server_error(request):
        raise RuntimeError("Fixture error")

    app.starlette.router.routes[:0] = [
        Route(f"/fixture/{name.replace('_', '-')}", handler)
        for name, handler in {
            "consent": consent, "consent_unknown": consent_unknown, "oauth_error": oauth_error,
            "sign_in": sign_in, "passkey": passkey, "passkey_failed": passkey_failed,
            "setup": setup, "pairing": pairing,
            "pairing_error": pairing_error, "token": token, "routine": routine, "group": group,
            "saved": saved, "expired": expired, "owner_key": owner_key, "server_error": server_error,
        }.items()
    ]
    async def service(scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/fixture/slack-approval":
            csrf = core.auth.csrf(Owner(ACCOUNT, "access").csrf_key).encode()
            scope = {**scope, "method": "POST", "path": "/slack/requests/example/approve",
                     "headers": (*scope["headers"], (b"x-csrf-token", csrf))}
        await app(scope, receive, send)

    uvicorn.run(service, host="127.0.0.1", port=PORT, access_log=False, log_level="critical")


if __name__ == "__main__":
    with TemporaryDirectory(prefix="swb-server-pages-") as directory:
        run(Path(directory))
