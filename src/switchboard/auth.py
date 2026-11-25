"""Single-owner OAuth 2.1 authorization server plus bearer verification for /mcp.

Clients identify themselves by Dynamic Client Registration (RFC 7591) or by a Client ID Metadata
Document (an https client_id). Every flow needs PKCE S256. Only the owner can approve a sign-in: /authorize sits
behind Cloudflare Access with the SSO identity provider, and the server verifies the Access JWT itself
(signature, audience, issuer, owner email). There is no password form.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode, urlparse

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import pages
from .config import Config
from .store import Store, dumps

log = logging.getLogger("switchboard.auth")

SCOPE = "switchboard"
CODE_TTL = 300
FORM_TTL = 900
REFRESH_REUSE_GRACE = 60
MAX_CLIENTS = 500
CIMD_MAX_BYTES = 64 * 1024
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64url_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@dataclass(frozen=True)
class Principal:
    """An authenticated caller: source is the agent tag (claude, chatgpt, or a key name), origin where it came in."""

    source: str
    client_id: str
    kind: str
    origin: str = "mcp"
    scopes: tuple[str, ...] | None = None

    def allows(self, scope: str) -> bool:
        """Whether this caller may use a memory scope ('general' or 'project:<slug>'); None means every scope."""
        return self.scopes is None or "*" in self.scopes or scope in self.scopes


@dataclass
class Client:
    client_id: str
    redirect_uris: list[str]
    auth_method: str
    name: str | None
    kind: str
    secret_hash: str | None = None


class OAuthError(Exception):
    def __init__(self, error: str, description: str, status: int = 400):
        super().__init__(description)
        self.error = error
        self.description = description
        self.status = status

    def response(self) -> JSONResponse:
        return JSONResponse({"error": self.error, "error_description": self.description}, status_code=self.status, headers={"Cache-Control": "no-store"})


def redirect_allowed(uri: str, patterns: tuple[str, ...]) -> bool:
    try:
        u = urlparse(uri)
    except ValueError:
        return False
    if u.fragment or not u.scheme or not u.netloc:
        return False
    loopback = u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1")
    if u.scheme != "https" and not loopback:
        return False
    for p in patterns:
        if p.endswith("/*"):
            pu = urlparse(p[:-2])
            if loopback and pu.scheme == "http" and pu.hostname == u.hostname:
                return True
            if u.scheme == pu.scheme and u.netloc == pu.netloc and (u.path + "/").startswith(pu.path.rstrip("/") + "/"):
                return True
        elif uri == p:
            return True
    return False


def redirect_matches(requested: str, registered: list[str]) -> bool:
    """Exact match, except that loopback redirects match on any port (RFC 8252 7.3)."""
    if requested in registered:
        return True
    rq = urlparse(requested)
    if rq.scheme != "http" or rq.hostname not in ("localhost", "127.0.0.1", "::1"):
        return False
    for r in registered:
        ru = urlparse(r)
        if ru.scheme == "http" and ru.hostname == rq.hostname and ru.path == rq.path and ru.query == rq.query:
            return True
    return False


def host_allowed(host: str | None, hosts: tuple[str, ...]) -> bool:
    host = (host or "").lower()
    return any(host == h or host.endswith("." + h) for h in hosts)


AGENT_LABELS = {"claude": "Claude", "chatgpt": "ChatGPT", "capy": "Capy"}


def source_for(redirect_uri: str, client: Client, names: dict[str, str]) -> str:
    host = (urlparse(redirect_uri).hostname or "").lower()
    cimd_host = (urlparse(client.client_id).hostname or "").lower() if client.kind == "cimd" else ""
    for needle, name in names.items():
        if needle and (needle in host or needle in cimd_host or needle.lower() in (client.name or "").lower()):
            return name
    if host_allowed(host, ("claude.ai", "claude.com")) or host_allowed(cimd_host, ("claude.ai", "claude.com", "anthropic.com")):
        return "claude"
    if host_allowed(host, ("chatgpt.com", "openai.com")) or host_allowed(cimd_host, ("chatgpt.com", "openai.com")):
        return "chatgpt"
    if host_allowed(host, ("capy.ai",)) or host_allowed(cimd_host, ("capy.ai",)):
        return "capy"
    slug = re.sub(r"[^a-z0-9]+", "-", (client.name or "").lower()).strip("-")[:40]
    return slug or "local"


class Auth:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        http: httpx.AsyncClient | None = None,
        access_verifier: Callable[[str], Awaitable[str | None]] | None = None,
        secret: bytes | None = None,
    ):
        self.cfg = cfg
        self.store = store
        self.http = http or httpx.AsyncClient(timeout=5.0, follow_redirects=False)
        self.secret = secret or secrets.token_bytes(32)
        self._access_verifier = access_verifier
        self._jwks: Any = None
        self._cimd_cache: dict[str, tuple[float, Client]] = {}

    # Bearer verification --------------------------------------------------------

    def verify_bearer(self, token: str | None) -> Principal | None:
        if not token:
            return None
        for key in self.cfg.static_keys:
            if hmac.compare_digest(sha(token), sha(key.secret)):
                return Principal(source=key.name, client_id=f"key:{key.name}", kind="key", scopes=self.cfg.key_scopes.get(key.name))
        if not self.cfg.resource:
            return None
        row = self.store.one(
            "SELECT * FROM oauth_tokens WHERE token_hash = ? AND kind = 'access' AND revoked_at IS NULL", (sha(token),)
        )
        if row is None or row["expires_at"] < self.store.clock() or row["resource"] != self.cfg.resource:
            return None
        return Principal(source=row["source"], client_id=row["client_id"], kind="oauth", scopes=self.cfg.key_scopes.get(row["source"]))

    def challenge_header(self, error: str | None = None) -> str:
        parts = []
        if self.cfg.public_url:
            parts.append(f'resource_metadata="{self.cfg.public_url}/.well-known/oauth-protected-resource"')
            parts.append(f'scope="{SCOPE}"')
        if error:
            parts.append(f'error="{error}"')
        return "Bearer " + ", ".join(parts) if parts else "Bearer"

    # Metadata -----------------------------------------------------------------

    def resource_metadata(self) -> dict[str, Any]:
        return {
            "resource": self.cfg.resource,
            "authorization_servers": [self.cfg.issuer],
            "scopes_supported": [SCOPE],
            "bearer_methods_supported": ["header"],
            "resource_name": "Switchboard",
        }

    def server_metadata(self) -> dict[str, Any]:
        base = self.cfg.issuer
        return {
            "issuer": base,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "registration_endpoint": f"{base}/register",
            "revocation_endpoint": f"{base}/revoke",
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "revocation_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "scopes_supported": [SCOPE],
            "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True,
        }

    # Clients --------------------------------------------------------------------

    async def register(self, body: dict[str, Any]) -> dict[str, Any]:
        uris = body.get("redirect_uris")
        if not isinstance(uris, list) or not uris or not all(isinstance(u, str) for u in uris):
            raise OAuthError("invalid_redirect_uri", "redirect_uris is required")
        bad = [u for u in uris if not redirect_allowed(u, self.cfg.redirect_allow)]
        if bad:
            raise OAuthError("invalid_redirect_uri", f"redirect URI not allowed by this server: {bad[0]}")
        method = body.get("token_endpoint_auth_method") or "none"
        if method not in ("none", "client_secret_post", "client_secret_basic"):
            raise OAuthError("invalid_client_metadata", "unsupported token_endpoint_auth_method")
        grants = body.get("grant_types") or ["authorization_code", "refresh_token"]
        if any(g not in ("authorization_code", "refresh_token") for g in grants):
            raise OAuthError("invalid_client_metadata", "only authorization_code and refresh_token grants are supported")
        name = str(body.get("client_name") or "")[:100] or None
        now = self.store.clock()
        self.store.x(
            "DELETE FROM oauth_clients WHERE kind = 'dcr' AND last_used_at IS NULL AND created_at < ?", (now - 7 * 86400,)
        )
        if (self.store.one("SELECT COUNT(*) AS n FROM oauth_clients")["n"]) >= MAX_CLIENTS:
            raise OAuthError("temporarily_unavailable", "too many registered clients", 503)
        client_id = "cb_" + secrets.token_urlsafe(18)
        secret = secrets.token_urlsafe(32) if method != "none" else None
        self.store.x(
            "INSERT INTO oauth_clients(client_id, secret_hash, name, redirect_uris, auth_method, kind, created_at) VALUES (?,?,?,?,?,?,?)",
            (client_id, sha(secret) if secret else None, name, dumps(uris), method, "dcr", now),
        )
        out = {
            "client_id": client_id,
            "client_id_issued_at": int(now),
            "redirect_uris": uris,
            "token_endpoint_auth_method": method,
            "grant_types": grants,
            "response_types": ["code"],
        }
        if name:
            out["client_name"] = name
        if secret:
            out["client_secret"] = secret
            out["client_secret_expires_at"] = 0
        return out

    async def client(self, client_id: str | None) -> Client:
        if not client_id:
            raise OAuthError("invalid_client", "client_id is required", 401)
        if client_id.startswith("https://"):
            return await self._cimd(client_id)
        row = self.store.one("SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,))
        if row is None:
            raise OAuthError("invalid_client", "unknown client", 401)
        return Client(
            client_id=row["client_id"],
            redirect_uris=json.loads(row["redirect_uris"]),
            auth_method=row["auth_method"],
            name=row["name"],
            kind=row["kind"],
            secret_hash=row["secret_hash"],
        )

    async def _cimd(self, url: str) -> Client:
        cached = self._cimd_cache.get(url)
        if cached and cached[0] > self.store.clock():
            return cached[1]
        u = urlparse(url)
        if u.scheme != "https" or not u.path or u.path == "/" or u.fragment or not host_allowed(u.hostname, self.cfg.cimd_hosts):
            raise OAuthError("invalid_client", "client metadata document host is not allowed", 401)
        try:
            async with self.http.stream("GET", url, headers={"Accept": "application/json"}) as r:
                if r.status_code != 200:
                    raise OAuthError("invalid_client", f"client metadata document answered {r.status_code}", 401)
                raw = b""
                async for chunk in r.aiter_bytes():
                    raw += chunk
                    if len(raw) > CIMD_MAX_BYTES:
                        raise OAuthError("invalid_client", "client metadata document too large", 401)
            doc = json.loads(raw)
        except OAuthError:
            raise
        except (httpx.HTTPError, ValueError) as e:
            raise OAuthError("invalid_client", f"client metadata document unreadable: {type(e).__name__}", 401) from e
        if not isinstance(doc, dict) or doc.get("client_id") != url:
            raise OAuthError("invalid_client", "client metadata document client_id does not match its URL", 401)
        uris = doc.get("redirect_uris")
        if not isinstance(uris, list) or not uris:
            raise OAuthError("invalid_client", "client metadata document has no redirect_uris", 401)
        method = doc.get("token_endpoint_auth_method") or "none"
        if method != "none":
            raise OAuthError("invalid_client", "only public (none) CIMD clients are supported", 401)
        c = Client(client_id=url, redirect_uris=[str(x) for x in uris], auth_method="none", name=str(doc.get("client_name") or "")[:100] or None, kind="cimd")
        self._cimd_cache[url] = (self.store.clock() + 3600, c)
        return c

    def authenticate_client(self, c: Client, secret: str | None) -> None:
        if c.auth_method == "none":
            return
        if not secret or not c.secret_hash or not hmac.compare_digest(sha(secret), c.secret_hash):
            raise OAuthError("invalid_client", "client authentication failed", 401)

    # Authorization ----------------------------------------------------------------

    def normalize_resource(self, resource: str | None) -> str:
        if not resource:
            return self.cfg.resource or ""
        r = resource.rstrip("/")
        if r in (self.cfg.resource, self.cfg.public_url):
            return self.cfg.resource or ""
        raise OAuthError("invalid_target", "unknown resource")

    def sign(self, params: dict[str, str]) -> str:
        payload = b64url(json.dumps({**params, "_t": int(self.store.clock())}, separators=(",", ":")).encode())
        mac = b64url(hmac.new(self.secret, payload.encode(), hashlib.sha256).digest())
        return f"{payload}.{mac}"

    def unsign(self, token: str) -> dict[str, str]:
        try:
            payload, mac = token.split(".", 1)
        except ValueError as e:
            raise OAuthError("invalid_request", "bad form token") from e
        good = b64url(hmac.new(self.secret, payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, good):
            raise OAuthError("invalid_request", "bad form token")
        data = json.loads(b64url_dec(payload))
        if data.pop("_t", 0) + FORM_TTL < self.store.clock():
            raise OAuthError("invalid_request", "sign-in form expired; start again from the app")
        return data

    async def access_email(self, request: Request) -> str | None:
        assertion = request.headers.get("cf-access-jwt-assertion")
        if not assertion or not self.cfg.access_team_domain:
            return None
        if self._access_verifier:
            email = await self._access_verifier(assertion)
        else:
            email = await asyncio.to_thread(self._verify_access_jwt, assertion)
        return email.lower() if email else None

    def _verify_access_jwt(self, assertion: str) -> str | None:
        import jwt

        team = self.cfg.access_team_domain
        if self._jwks is None:
            self._jwks = jwt.PyJWKClient(f"https://{team}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)
        try:
            key = self._jwks.get_signing_key_from_jwt(assertion)
            claims = jwt.decode(assertion, key.key, algorithms=["RS256"], audience=self.cfg.access_aud, issuer=f"https://{team}")
        except Exception as e:
            log.warning("cloudflare access assertion rejected: %s", type(e).__name__)
            return None
        return claims.get("email")

    async def owner(self, request: Request) -> str | None:
        """The owner's email when the request carries a valid Access JWT for an owner, else None."""
        email = await self.access_email(request)
        return email if email and email in self.cfg.owner_emails else None

    async def validate_authorize(self, p: dict[str, str]) -> tuple[Client, dict[str, str]]:
        """Checks that must pass before anything may be redirected to the client."""
        if not self.cfg.issuer:
            raise OAuthError("temporarily_unavailable", "public_url is not configured", 503)
        c = await self.client(p.get("client_id"))
        redirect_uri = p.get("redirect_uri") or (c.redirect_uris[0] if len(c.redirect_uris) == 1 else "")
        if not redirect_uri or not redirect_matches(redirect_uri, c.redirect_uris):
            raise OAuthError("invalid_request", "redirect_uri is not registered for this client")
        if not redirect_allowed(redirect_uri, self.cfg.redirect_allow):
            raise OAuthError("invalid_request", "redirect_uri is not allowed by this server")
        return c, {**p, "redirect_uri": redirect_uri}

    def check_request(self, p: dict[str, str]) -> None:
        """Checks whose failures are reported back to the client's redirect URI."""
        if p.get("response_type") != "code":
            raise OAuthError("unsupported_response_type", "response_type must be code")
        if p.get("code_challenge_method") != "S256" or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", p.get("code_challenge") or ""):
            raise OAuthError("invalid_request", "PKCE with code_challenge_method=S256 is required")
        self.normalize_resource(p.get("resource"))

    def error_redirect(self, redirect_uri: str, state: str | None, e: OAuthError) -> RedirectResponse:
        q = {"error": e.error, "error_description": e.description, "iss": self.cfg.issuer or ""}
        if state:
            q["state"] = state
        return RedirectResponse(_append(redirect_uri, q), status_code=302)

    def issue_code(self, c: Client, p: dict[str, str]) -> str:
        code = secrets.token_urlsafe(32)
        source = source_for(p["redirect_uri"], c, self.cfg.source_names)
        self.store.x(
            "INSERT INTO oauth_codes(code_hash, client_id, redirect_uri, challenge, resource, scope, source, expires_at) VALUES (?,?,?,?,?,?,?,?)",
            (sha(code), c.client_id, p["redirect_uri"], p["code_challenge"], self.normalize_resource(p.get("resource")), SCOPE, source, self.store.clock() + CODE_TTL),
        )
        self.store.x("UPDATE oauth_clients SET last_used_at = ? WHERE client_id = ?", (self.store.clock(), c.client_id))
        return code

    # Tokens -----------------------------------------------------------------------

    def _issue(self, client_id: str, resource: str, source: str, family: str | None = None) -> dict[str, Any]:
        now = self.store.clock()
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        family = family or secrets.token_hex(8)
        for tok, kind, ttl in ((access, "access", self.cfg.access_token_ttl), (refresh, "refresh", self.cfg.refresh_token_ttl)):
            self.store.x(
                "INSERT INTO oauth_tokens(token_hash, kind, client_id, resource, scope, source, family, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                (sha(tok), kind, client_id, resource, SCOPE, source, family, now + ttl),
            )
        self.store.x("DELETE FROM oauth_tokens WHERE expires_at < ?", (now - 86400,))
        return {"access_token": access, "token_type": "Bearer", "expires_in": self.cfg.access_token_ttl, "refresh_token": refresh, "scope": SCOPE}

    async def token(self, form: dict[str, str], basic: tuple[str, str] | None) -> dict[str, Any]:
        client_id = form.get("client_id")
        secret = form.get("client_secret")
        if basic:
            client_id, secret = basic
        c = await self.client(client_id)
        if basic and c.auth_method != "client_secret_basic" and c.auth_method != "none":
            raise OAuthError("invalid_client", "client must use its registered authentication method", 401)
        self.authenticate_client(c, secret)
        grant = form.get("grant_type")
        now = self.store.clock()
        if grant == "authorization_code":
            code = form.get("code") or ""
            row = self.store.one("SELECT * FROM oauth_codes WHERE code_hash = ?", (sha(code),))
            self.store.x("DELETE FROM oauth_codes WHERE code_hash = ? OR expires_at < ?", (sha(code), now))
            if row is None or row["expires_at"] < now or row["client_id"] != c.client_id:
                raise OAuthError("invalid_grant", "authorization code is invalid or expired")
            if form.get("redirect_uri") and form["redirect_uri"] != row["redirect_uri"]:
                raise OAuthError("invalid_grant", "redirect_uri does not match")
            verifier = form.get("code_verifier") or ""
            if not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier) or b64url(hashlib.sha256(verifier.encode()).digest()) != row["challenge"]:
                raise OAuthError("invalid_grant", "PKCE verification failed")
            if form.get("resource") and self.normalize_resource(form["resource"]) != row["resource"]:
                raise OAuthError("invalid_target", "resource does not match the authorization")
            return self._issue(c.client_id, row["resource"], row["source"])
        if grant == "refresh_token":
            row = self.store.one("SELECT * FROM oauth_tokens WHERE token_hash = ? AND kind = 'refresh'", (sha(form.get("refresh_token") or ""),))
            if row is None or row["client_id"] != c.client_id or row["expires_at"] < now:
                raise OAuthError("invalid_grant", "refresh token is invalid or expired")
            if row["revoked_at"] is not None:
                if now - row["revoked_at"] > REFRESH_REUSE_GRACE:
                    self.store.x("UPDATE oauth_tokens SET revoked_at = ? WHERE family = ? AND revoked_at IS NULL", (now, row["family"]))
                raise OAuthError("invalid_grant", "refresh token was already used")
            if form.get("resource") and self.normalize_resource(form["resource"]) != row["resource"]:
                raise OAuthError("invalid_target", "resource does not match the grant")
            self.store.x("UPDATE oauth_tokens SET revoked_at = ? WHERE token_hash = ?", (now, row["token_hash"]))
            return self._issue(c.client_id, row["resource"], row["source"], family=row["family"])
        raise OAuthError("unsupported_grant_type", "grant_type must be authorization_code or refresh_token")

    def revoke(self, token: str, client_id: str | None) -> None:
        row = self.store.one("SELECT * FROM oauth_tokens WHERE token_hash = ?", (sha(token),))
        if row and (client_id is None or row["client_id"] == client_id):
            self.store.x("UPDATE oauth_tokens SET revoked_at = ? WHERE family = ? AND revoked_at IS NULL", (self.store.clock(), row["family"]))

    # HTTP routes ----------------------------------------------------------------------

    def routes(self) -> list[Route]:
        return [
            Route("/.well-known/oauth-protected-resource", self.h_prm, methods=["GET"]),
            Route("/.well-known/oauth-protected-resource/mcp", self.h_prm, methods=["GET"]),
            Route("/.well-known/oauth-authorization-server", self.h_asm, methods=["GET"]),
            Route("/.well-known/openid-configuration", self.h_asm, methods=["GET"]),
            Route("/register", self.h_register, methods=["POST"]),
            Route("/authorize", self.h_authorize, methods=["GET", "POST"]),
            Route("/token", self.h_token, methods=["POST"]),
            Route("/revoke", self.h_revoke, methods=["POST"]),
        ]

    def _disabled(self) -> JSONResponse | None:
        if not self.cfg.issuer:
            return JSONResponse({"error": "oauth disabled: public_url is not configured"}, status_code=404)
        return None

    async def h_prm(self, request: Request) -> Response:
        return self._disabled() or JSONResponse(self.resource_metadata())

    async def h_asm(self, request: Request) -> Response:
        return self._disabled() or JSONResponse(self.server_metadata())

    async def h_register(self, request: Request) -> Response:
        if (d := self._disabled()) is not None:
            return d
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return OAuthError("invalid_client_metadata", "body must be a JSON object").response()
        try:
            out = await self.register(body)
        except OAuthError as e:
            return e.response()
        log.info("registered oauth client %s (%s)", out["client_id"], out.get("client_name") or "unnamed")
        return JSONResponse(out, status_code=201, headers={"Cache-Control": "no-store"})

    async def h_authorize(self, request: Request) -> Response:
        if (d := self._disabled()) is not None:
            return d
        if request.method == "GET":
            params = dict(request.query_params)
        else:
            form = await request.form()
            try:
                params = self.unsign(str(form.get("req") or ""))
            except OAuthError as e:
                return pages.message_page("Sign-in failed", e.description, 400)
        try:
            c, params = await self.validate_authorize(params)
        except OAuthError as e:
            return pages.message_page("Sign-in failed", e.description, e.status if e.status != 401 else 400)
        state = params.get("state")
        try:
            self.check_request(params)
        except OAuthError as e:
            return self.error_redirect(params["redirect_uri"], state, e)

        if not self.cfg.access_team_domain:
            return pages.message_page("Sign-in disabled", "Sign-in needs Cloudflare Access in front of /authorize (auth.cloudflare_access).", 503)
        email = await self.owner(request)
        if email is None:
            return pages.message_page("Not allowed", "This sign-in needs the owner's SSO through Cloudflare Access.", 403)

        if request.method == "POST":
            form = await request.form()
            if form.get("decision") == "deny":
                return self.error_redirect(params["redirect_uri"], state, OAuthError("access_denied", "the owner denied the request"))
            if form.get("decision") == "approve":
                code = self.issue_code(c, params)
                log.info("owner approved oauth client %s", c.client_id[:60])
                q = {"code": code, "iss": self.cfg.issuer or ""}
                if state:
                    q["state"] = state
                return RedirectResponse(_append(params["redirect_uri"], q), status_code=302)

        source = source_for(params["redirect_uri"], c, self.cfg.source_names)
        who = html.escape(AGENT_LABELS.get(source) or c.name or c.client_id)
        dest = html.escape(urlparse(params["redirect_uri"]).netloc)
        line = f"<b>{who}</b> wants to connect as <span class=\"tag\">{html.escape(source)}</span>. It can read and write shared memory and route work to the destinations this switchboard connects, such as Capy."
        signed = f'<p class="item-line">Signed in with SSO as {html.escape(email)}</p>'
        body = pages.hero("Connect to Switchboard", line) + f"""
<form method="post" class="fade-in">
<input type="hidden" name="req" value="{html.escape(self.sign(params))}">
<div class="card"><div class="item"><div class="item-main"><span class="card-title">{who}</span><span class="item-line">Returns to <code>{dest}</code></span>{signed}</div></div></div>
<div class="actions"><button class="pill-button primary" name="decision" value="approve">Approve</button>
<button class="text-button" name="decision" value="deny" formnovalidate>Deny</button></div>
</form>"""
        return pages.page("Connect", body, 200)

    async def h_token(self, request: Request) -> Response:
        if (d := self._disabled()) is not None:
            return d
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("application/json"):
            try:
                form = {k: str(v) for k, v in (await request.json()).items()}
            except (ValueError, AttributeError):
                return OAuthError("invalid_request", "bad JSON").response()
        else:
            form = {k: str(v) for k, v in (await request.form()).items()}
        try:
            out = await self.token(form, _basic(request))
        except OAuthError as e:
            resp = e.response()
            if e.status == 401:
                resp.headers["WWW-Authenticate"] = "Basic"
            return resp
        return JSONResponse(out, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    async def h_revoke(self, request: Request) -> Response:
        if (d := self._disabled()) is not None:
            return d
        form = {k: str(v) for k, v in (await request.form()).items()}
        basic = _basic(request)
        try:
            c = await self.client(basic[0] if basic else form.get("client_id"))
            self.authenticate_client(c, basic[1] if basic else form.get("client_secret"))
        except OAuthError as e:
            return e.response()
        self.revoke(form.get("token") or "", c.client_id)
        return Response(status_code=200)


def _basic(request: Request) -> tuple[str, str] | None:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("basic "):
        return None
    try:
        from urllib.parse import unquote

        user, _, pw = base64.b64decode(h[6:]).decode().partition(":")
        return unquote(user), unquote(pw)
    except (ValueError, UnicodeDecodeError):
        return None


def _append(uri: str, q: dict[str, str]) -> str:
    return uri + ("&" if urlparse(uri).query else "?") + urlencode(q)

