from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, parse_authentication_credential_json
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from . import pages
from .guard import RateLimiter, client_ip
from .oidc import OIDC
from .sessions import CHALLENGE_COOKIE, CHALLENGE_TTL, clear_cookie, safe_next, set_cookie
from .web import spa_index

if TYPE_CHECKING:
    from .auth import Auth

log = logging.getLogger(__name__)


def error(name: str, status: int) -> JSONResponse:
    return JSONResponse({"error": name}, status_code=status)


async def json_body(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


class OwnerLogin:
    def __init__(self, auth: Auth, limiter: RateLimiter):
        self.auth = auth
        self.store = auth.store
        self.sessions = auth.sessions
        self.limiter = limiter
        self.oidc = OIDC(auth)
        self.setup_digest = auth.cfg.owner_setup_digest
        self.setup_throttled = 0

    @property
    def origin(self) -> str | None:
        return self.auth.cfg.public_origin

    @property
    def rp_id(self) -> str | None:
        return self.auth.cfg.passkey_rp

    def methods(self) -> dict:
        methods = {"passkeys": self.store.one("SELECT count(*) AS n FROM owner_passkeys WHERE rp_id = ? OR (rp_id IS NULL AND ?)",
                                             (self.rp_id, self.rp_id == self.auth.cfg.legacy_passkey_rp))["n"],
                   "oidc": {"name": self.oidc.cfg.name} if self.oidc.cfg else None,
                   "access": self.auth.cfg.access_configured}
        if self.setup_open():
            methods["setup"] = True
        return methods

    def setup_open(self) -> bool:
        return (self.setup_digest is not None and self.origin is not None and self.oidc.cfg is None
                and not self.auth.cfg.access_configured and self.store.one("SELECT 1 FROM owner_initialized") is None)

    def routes(self) -> list[Route]:
        return [
            pages.PageRoute("/login", self.login, pages.LOGIN_TITLE, methods=["GET"]),
            Route("/auth/session", self.session, methods=["GET"]),
            Route("/auth/logout", self.logout, methods=["POST"]),
            Route("/auth/link", self.link, methods=["POST"]),
            Route("/auth/setup", self.setup, methods=["POST"]),
            Route("/auth/passkey/login/options", self.login_options, methods=["POST"]),
            Route("/auth/passkey/login/verify", self.login_verify, methods=["POST"]),
            Route("/auth/passkey/register/options", self.register_options, methods=["POST"]),
            Route("/auth/passkey/register/verify", self.register_verify, methods=["POST"]),
            Route("/auth/passkeys", self.passkeys, methods=["GET"]),
            Route("/auth/passkeys/{id}", self.remove_passkey, methods=["DELETE"]),
            Route("/auth/oidc/start", self.oidc.start, methods=["GET"]),
            Route("/auth/oidc/callback", self.oidc.callback, methods=["GET"]),
        ]

    async def login(self, request: Request) -> Response:
        next_path = safe_next(request.query_params.get("next"))
        owner = await self.auth.owner(request)
        if owner is not None and owner.via == "access":
            return RedirectResponse(next_path, status_code=303)
        if (index := spa_index()) is not None:
            return index
        return pages.login_page(self.methods(), next_path, request.query_params.get("error"), signed_in=owner is not None)

    async def session(self, request: Request) -> Response:
        owner = await self.auth.owner(request)
        return JSONResponse({"signed_in": owner is not None, "via": owner.via if owner else None,
                             "csrf": self.auth.csrf(owner.csrf_key) if owner else None, "methods": self.methods()})

    async def logout(self, request: Request) -> Response:
        _, refused = await self.auth.protect_owner(request)
        return refused if refused is not None else self.sessions.logout(request, Response(status_code=204))

    def limited(self, bucket: str, ip: str) -> Response | None:
        wait = self.limiter.blocked(bucket, ip)
        if wait is None:
            return None
        return JSONResponse({"error": "slow_down"}, status_code=429, headers={"Retry-After": str(int(wait + 0.999))})

    async def link(self, request: Request) -> Response:
        if not self.auth.origin_ok(request):
            return error("csrf", 403)
        ip = client_ip(request.scope)
        if (limited := self.limited("login_link_fail", ip)) is not None:
            return limited
        token = (await json_body(request)).get("token")
        row, refused = (self.sessions.consume_link(token)
                        if self.origin and isinstance(token, str) and len(token) == 43 and token.isascii()
                        else (None, "link_expired"))
        if refused is not None:
            self.limiter.hit("login_link_fail", ip)
            return error(refused, 401)
        response = self.sessions.start(request, Response(status_code=204), "link", row["client_id"], row["source"])
        self.auth.audit.record(origin="http", source=row["source"], client=row["client_id"], destination="core",
                               action="login_link_used", outcome="ok")
        return response

    async def setup(self, request: Request) -> Response:
        if not self.setup_open():
            return error("setup_closed", 404)
        if not request.headers.get("origin") or not self.auth.origin_ok(request):
            return error("csrf", 403)
        code = (await json_body(request)).get("code")
        if not (isinstance(code, str) and len(code) in (44, 64) and code.isascii()
                and hmac.compare_digest(hashlib.sha256(code.encode()).digest(), self.setup_digest)):
            limited = self.limited("owner_setup_total", "*")
            self.limiter.hit("owner_setup_total", "*")
            if limited is None:
                peer = request.client.host if request.client else "?"
                limited = self.limited("owner_setup", peer)
                self.limiter.hit("owner_setup", peer)
            if limited is not None:
                self.setup_throttled += 1
                log.warning("Owner setup attempt rate limited (%d total)", self.setup_throttled)
                return limited
            return error("setup_failed", 401)
        response = self.sessions.start(request, Response(status_code=204), "setup")
        if response.status_code == 409:
            return error("setup_closed", 409)
        self.auth.audit.record(origin="http", source="owner", client="setup", destination="core", action="owner_setup", outcome="ok")
        return response

    async def login_options(self, request: Request) -> Response:
        if not self.auth.origin_ok(request):
            return error("csrf", 403)
        if not self.rp_id:
            return error("passkey_failed", 401)
        ip = client_ip(request.scope)
        if (limited := self.limited("passkey_options", ip)) is not None:
            return limited
        self.limiter.hit("passkey_options", ip)
        cookie, challenge = self.sessions.challenge("login", None)
        options = generate_authentication_options(rp_id=self.rp_id, challenge=challenge, timeout=CHALLENGE_TTL * 1000,
                                                  user_verification=UserVerificationRequirement.REQUIRED)
        response = JSONResponse({"publicKey": json.loads(options_to_json(options))})
        set_cookie(response, CHALLENGE_COOKIE, cookie, CHALLENGE_TTL)
        return response

    async def register_options(self, request: Request) -> Response:
        owner, refused = await self.auth.protect_owner(request)
        if refused is not None:
            return refused
        if not self.rp_id:
            return error("passkey_failed", 401)
        cookie, challenge = self.sessions.challenge("register", owner)
        options = generate_registration_options(
            rp_id=self.rp_id, rp_name="SwB", user_id=self.sessions.user_handle(), user_name="owner",
            user_display_name="Owner", challenge=challenge, timeout=CHALLENGE_TTL * 1000,
            authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,
                                                                    user_verification=UserVerificationRequirement.REQUIRED),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(row["id"]))
                                 for row in self.store.q("SELECT id FROM owner_passkeys")],
        )
        response = JSONResponse({"publicKey": json.loads(options_to_json(options))})
        set_cookie(response, CHALLENGE_COOKIE, cookie, CHALLENGE_TTL)
        return response

    async def login_verify(self, request: Request) -> Response:
        if not self.auth.origin_ok(request):
            return error("csrf", 403)
        challenge = self.sessions.consume_challenge(request, "login", None)
        ip = client_ip(request.scope)
        response = self.limited("passkey_fail", ip)
        if response is None:
            verified = None
            if self.rp_id and challenge:
                try:
                    credential = parse_authentication_credential_json((await json_body(request)).get("credential"))
                except Exception:
                    credential = None
                row = self.store.one("SELECT public_key FROM owner_passkeys WHERE id = ?", (credential.id,)) if credential else None
                user = self.store.one("SELECT handle FROM owner_webauthn_user WHERE id = 1")
                if (row is not None and user is not None and credential.response.user_handle is not None
                        and hmac.compare_digest(credential.response.user_handle, user["handle"])):
                    try:
                        verified = verify_authentication_response(
                            credential=credential, expected_challenge=challenge, expected_rp_id=self.rp_id,
                            expected_origin=self.origin, credential_public_key=row["public_key"],
                            credential_current_sign_count=0, require_user_verification=True,
                        )
                    except Exception:
                        pass
            row_id = bytes_to_base64url(verified.credential_id) if verified else None
            if verified and self.store.n(
                "UPDATE owner_passkeys SET last_used_at = ?, sign_count = max(sign_count, ?), rp_id = coalesce(rp_id, ?) "
                "WHERE id = ? AND public_key = ? AND ((sign_count = 0 AND ? = 0) OR sign_count < ?)",
                (self.store.clock(), verified.new_sign_count, self.rp_id, bytes_to_base64url(verified.credential_id),
                 row["public_key"], verified.new_sign_count, verified.new_sign_count),
            ):
                response = self.sessions.start(request, Response(status_code=204), "passkey", source=row_id)
            else:
                self.limiter.hit("passkey_fail", ip)
                response = error("passkey_failed", 401)
        clear_cookie(response, CHALLENGE_COOKIE)
        return response

    async def register_verify(self, request: Request) -> Response:
        if not self.auth.origin_ok(request):
            return error("csrf", 403)
        owner, refused = await self.auth.protect_owner(request)
        challenge = self.sessions.consume_challenge(request, "register", owner)
        if refused is not None:
            clear_cookie(refused, CHALLENGE_COOKIE)
            return refused
        ip = client_ip(request.scope)
        response = self.limited("passkey_fail", ip)
        if response is None:
            data = await json_body(request)
            name = data.get("name")
            verified = None
            if self.rp_id and challenge and isinstance(name, str) and name.strip():
                try:
                    verified = verify_registration_response(
                        credential=data.get("credential"), expected_challenge=challenge, expected_rp_id=self.rp_id,
                        expected_origin=self.origin, require_user_verification=True,
                    )
                except Exception:
                    pass
            if verified and self.store.n(
                "INSERT OR IGNORE INTO owner_passkeys(id, name, public_key, sign_count, created_at, rp_id) VALUES (?,?,?,?,?,?)",
                (cid := bytes_to_base64url(verified.credential_id), name.strip()[:100], verified.credential_public_key,
                 verified.sign_count, self.store.clock(), self.rp_id),
            ):
                response = JSONResponse({"id": cid}, status_code=201)
            else:
                self.limiter.hit("passkey_fail", ip)
                response = error("passkey_failed", 401)
        clear_cookie(response, CHALLENGE_COOKIE)
        return response

    async def passkeys(self, request: Request) -> Response:
        _, refused = await self.auth.protect_owner(request)
        if refused is not None:
            return refused
        return JSONResponse([dict(row) for row in self.store.q(
            "SELECT id, name, created_at, last_used_at FROM owner_passkeys ORDER BY created_at, id"
        )])

    async def remove_passkey(self, request: Request) -> Response:
        _, refused = await self.auth.protect_owner(request)
        if refused is not None:
            return refused
        cred_id = request.path_params["id"]

        def delete(db):
            if not db.execute("SELECT 1 FROM owner_passkeys WHERE id = ?", (cred_id,)).fetchone():
                return 404
            if not (self.auth.cfg.access_configured or self.oidc.cfg) and db.execute(
                "SELECT count(*) FROM owner_passkeys"
            ).fetchone()[0] == 1:
                return 409
            db.execute("DELETE FROM owner_passkeys WHERE id = ?", (cred_id,))
            db.execute("DELETE FROM owner_sessions WHERE via = 'passkey' AND source = ?", (cred_id,))
            return 204

        status = self.store.tx(delete)
        return Response(status_code=204) if status == 204 else error("last_method" if status == 409 else "not_found", status)
