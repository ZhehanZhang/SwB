from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import TYPE_CHECKING
from urllib.parse import quote_plus, urlencode, urlsplit

import httpx
import jwt
from starlette.requests import Request
from starlette.responses import RedirectResponse

from .sessions import clear_cookie, safe_next, set_cookie

if TYPE_CHECKING:
    from .auth import Auth

COOKIE = "__Host-swb_oidc"
TTL = 300
FAILED = "/login?error=oidc_failed"
ALGS = ("RS256", "ES256")
FAILURES = (ValueError, TypeError, KeyError, OverflowError, httpx.InvalidURL, httpx.HTTPError, jwt.PyJWTError)


def _matches(value: object, expected: str) -> bool:
    return isinstance(value, str) and hmac.compare_digest(value.encode(), expected.encode())


class OIDC:
    def __init__(self, auth: Auth):
        self.auth = auth
        self.cfg = auth.cfg.oidc

    @property
    def callback_url(self) -> str:
        return f"{self.auth.cfg.public_url}/auth/oidc/callback"

    def _mac(self, payload: str) -> str:
        return hmac.digest(self.auth.secret, f"switchboard-oidc:{payload}".encode(), "sha256").hex()

    async def _get(self, url: str) -> dict:
        response = await self.auth.http.get(url, follow_redirects=False)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError
        return data

    async def _discovery(self) -> dict:
        data = await self._get(f"{self.cfg.issuer.rstrip('/')}/.well-known/openid-configuration")
        if not _matches(data.get("issuer"), self.cfg.issuer):
            raise ValueError
        for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            endpoint = data.get(name)
            if not isinstance(endpoint, str):
                raise ValueError
            url = urlsplit(endpoint)
            if url.scheme != "https" or not url.hostname or url.username is not None or "#" in endpoint:
                raise ValueError
        return data

    async def start(self, request: Request) -> RedirectResponse:
        from .auth import b64url

        if self.cfg is None:
            return RedirectResponse(FAILED, status_code=303)
        try:
            discovery = await self._discovery()
        except FAILURES:
            return RedirectResponse(FAILED, status_code=303)
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        payload = b64url(json.dumps({"state": state, "nonce": nonce, "verifier": verifier,
                                     "next": safe_next(request.query_params.get("next")),
                                     "expires": self.auth.store.clock() + TTL}).encode())
        params = {
            "response_type": "code", "client_id": self.cfg.client_id, "redirect_uri": self.callback_url,
            "scope": "openid email" if self.auth.cfg.owner_emails else "openid",
            "state": state, "nonce": nonce, "code_challenge_method": "S256",
            "code_challenge": b64url(hashlib.sha256(verifier.encode()).digest()),
        }
        endpoint = discovery["authorization_endpoint"]
        response = RedirectResponse(endpoint + ("&" if "?" in endpoint else "?") + urlencode(params), status_code=303)
        set_cookie(response, COOKIE, f"{payload}.{self._mac(payload)}", TTL)
        return response

    async def _claims(self, discovery: dict, code: str, verifier: str) -> dict:
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": self.callback_url,
                "code_verifier": verifier}
        methods = discovery.get("token_endpoint_auth_methods_supported", ["client_secret_basic"])
        if not isinstance(methods, list):
            raise ValueError
        secret = self.auth.cfg.env[self.cfg.client_secret_env]
        if "client_secret_basic" in methods:
            auth = httpx.BasicAuth(quote_plus(self.cfg.client_id), quote_plus(secret))
        elif "client_secret_post" in methods:
            data.update(client_id=self.cfg.client_id, client_secret=secret)
            auth = None
        else:
            raise ValueError
        response = await self.auth.http.post(discovery["token_endpoint"], data=data, auth=auth, follow_redirects=False)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not isinstance(token := body.get("id_token"), str):
            raise ValueError
        header = jwt.get_unverified_header(token)
        advertised = discovery.get("id_token_signing_alg_values_supported")
        alg = header.get("alg")
        if alg not in ALGS or not isinstance(advertised, list) or alg not in advertised:
            raise ValueError
        jwks = (await self._get(discovery["jwks_uri"])).get("keys")
        if not isinstance(jwks, list):
            raise ValueError
        kid = header.get("kid")
        keys = [key for key in jwks if isinstance(key, dict) and key.get("use") in (None, "sig")
                and key.get("alg") in (None, alg) and (kid is None or key.get("kid") == kid)]
        if len(keys) != 1:
            raise ValueError
        return jwt.decode(token, jwt.PyJWK.from_dict(keys[0], algorithm=alg).key, algorithms=[alg],
                          issuer=self.cfg.issuer, audience=self.cfg.client_id,
                          options={"require": ["iss", "aud", "exp", "sub", "nonce"]})

    async def callback(self, request: Request) -> RedirectResponse:
        from .auth import b64url_dec

        response = RedirectResponse(FAILED, status_code=303)
        if self.cfg is not None:
            try:
                payload, sep, sig = request.cookies.get(COOKIE, "").partition(".")
                if not sep or not _matches(sig, self._mac(payload)):
                    raise ValueError
                flow = json.loads(b64url_dec(payload))
                code = request.query_params.get("code")
                if (not code or "error" in request.query_params or flow["expires"] <= self.auth.store.clock()
                        or not _matches(request.query_params.get("state"), flow["state"])):
                    raise ValueError
                response_issuer = request.query_params.get("iss")
                if response_issuer is not None and not _matches(response_issuer, self.cfg.issuer):
                    raise ValueError
                claims = await self._claims(await self._discovery(), code, flow["verifier"])
                if not _matches(claims.get("nonce"), flow["nonce"]):
                    raise ValueError
                audience, azp = claims["aud"], claims.get("azp")
                if (azp is not None or isinstance(audience, list) and len(audience) > 1) and not _matches(azp, self.cfg.client_id):
                    raise ValueError
                email = claims.get("email")
                if not any(_matches(claims["sub"], s) for s in self.cfg.owner_subjects) and not (
                    claims.get("email_verified") is True and isinstance(email, str)
                    and any(_matches(email.lower(), allowed) for allowed in self.auth.cfg.owner_emails)
                ):
                    raise ValueError
            except FAILURES:
                pass
            else:
                response = RedirectResponse(safe_next(flow["next"]), status_code=303)
                self.auth.sessions.start(request, response, "oidc")
        clear_cookie(response, COOKIE)
        return response
