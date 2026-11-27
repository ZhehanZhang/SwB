"""Single-owner OAuth 2.1 authorization server plus bearer verification for /mcp.

Clients identify themselves by Dynamic Client Registration (RFC 7591) or by a Client ID Metadata
Document (an https client_id). Every flow needs PKCE S256. Only the owner can approve a sign-in,
using an owner session or a verified Cloudflare Access assertion. There is no password form.
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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from urllib.parse import unquote_plus, urlencode, urlparse

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import pages
from .agents import PRESETS, Agents, host_allowed
from .config import Config, LOOPBACK_HOSTS
from .guard import audit_client_ip
from .refusals import CSRF_AUDIT_PATHS
from .sessions import Owner, Sessions, SESSION_COOKIE, login_redirect
from .store import Store, dumps

if TYPE_CHECKING:
    from .visibility import Who

log = logging.getLogger("switchboard.auth")

SCOPE = "switchboard"
COMPANION_KEY_PREFIX = "swbd_"
CODE_TTL = 300
FORM_TTL = 900
REFRESH_REUSE_GRACE = 60
MAX_CLIENTS = 500
CIMD_MAX_BYTES = 64 * 1024


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
    trust: str = "owner"
    tools: tuple[str, ...] | None = None
    targets: tuple[str, ...] | None = None

    @property
    def is_unrestricted_owner(self) -> bool:
        return (self.kind in ("key", "oauth") and self.trust == "owner"
                and self.scopes is None and self.tools is None and self.targets is None)
    credential: str | None = field(default=None, repr=False, compare=False)
    memory_grants: tuple[str, ...] = field(default=(), repr=False)
    visibility_who: Who | None = field(default=None, repr=False)

    @property
    def is_memory_owner(self) -> bool:
        return self.kind == "owner_session" or self.is_unrestricted_owner

    @property
    def memory_writer(self) -> str | None:
        if self.is_memory_owner:
            return None
        if self.kind == "connect" or (self.kind in ("key", "oauth", "companion") and self.credential is not None):
            return sha(dumps([self.kind, self.client_id, self.credential]))
        return None

    def allows(self, scope: str) -> bool:
        """Whether this caller has a grant for the memory scope."""
        if self.kind == "connect":
            return scope in self.memory_grants
        return self.allows_routing_scope(scope)

    def allows_private_memory(self, scope: str) -> bool:
        grants = self.memory_grants if self.kind == "connect" else self.scopes
        return self.is_memory_owner or scope in (grants or ())

    def allows_routing_scope(self, scope: str) -> bool:
        """The configured conversation, route and provider scope limit."""
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
    loopback = u.scheme == "http" and u.hostname in LOOPBACK_HOSTS
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
    if rq.scheme != "http" or rq.hostname not in LOOPBACK_HOSTS:
        return False
    for r in registered:
        ru = urlparse(r)
        if ru.scheme == "http" and ru.hostname == rq.hostname and ru.path == rq.path and ru.query == rq.query:
            return True
    return False


def source_for(redirect_uri: str, client: Client, names: dict[str, str], agents: Agents | None = None) -> str:
    """The agent an OAuth client signs in as: auth.source_names first, then the agents registry, then its client name."""
    host = (urlparse(redirect_uri).hostname or "").lower()
    cimd_url = client.client_id if client.kind == "cimd" else ""
    cimd_host = (urlparse(cimd_url).hostname or "").lower()
    for needle, name in names.items():
        if needle and (needle in host or needle in cimd_host or needle.lower() in (client.name or "").lower()):
            return name
    matched = (agents or Agents(PRESETS.values())).match(host, cimd_url)
    if matched:
        return matched
    slug = re.sub(r"[^a-z0-9]+", "-", (client.name or "").lower()).strip("-")[:40]
    return slug or f"oauth-{sha(client.client_id)[:12]}"


class Auth:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        http: httpx.AsyncClient | None = None,
        access_verifier: Callable[[str], Awaitable[str | None]] | None = None,
        secret: bytes | None = None,
        agents: Agents | None = None,
        connections: Any = None,
    ):
        self.cfg = cfg
        self.store = store
        self.agents = agents or Agents(PRESETS.values())
        self.connections = connections
        self.redirect_allow = cfg.redirect_allow or self.agents.redirect_uris()
        self.cimd_hosts = cfg.cimd_hosts or self.agents.cimd_hosts()
        self.http = http or httpx.AsyncClient(timeout=5.0, follow_redirects=False)
        self.secret = secret or secrets.token_bytes(32)
        self.sessions = Sessions(store)
        self._access_verifier = access_verifier
        self._jwks: Any = None
        self._cimd_cache: dict[str, tuple[float, Client]] = {}
        self.audit: Any = None

    def failed(self, request: Request, endpoint: str, e: "OAuthError", client: str | None = None) -> None:
        """Record a refused OAuth step with its reason. Reasons name redirect URIs and hosts, never secrets."""
        log.info("oauth %s refused: %s", endpoint, e.description)
        if self.audit is not None:
            from .guard import client_ip

            self.audit.record(origin="http", source="-", client=client_ip(request.scope), destination="core", action="oauth_refused",
                              outcome="refused", target=endpoint, detail=f"{e.error}: {e.description}"[:500], ref=(client or "")[:200] or None)

    # Bearer verification --------------------------------------------------------

    def _limits(self, agent: str) -> dict[str, Any]:
        return {"scopes": self.cfg.key_scopes.get(agent), "tools": self.cfg.key_tools.get(agent), "targets": self.cfg.key_targets.get(agent)}

    def _key_grant(self, key: str) -> str:
        return hmac.digest(self.secret, key.encode(), "sha256").hex()

    def verify_bearer(self, token: str | None) -> Principal | None:
        if not token or token.startswith(COMPANION_KEY_PREFIX):
            return None
        for key in self.cfg.static_keys:
            if hmac.compare_digest(sha(token), sha(key.secret)):
                if key.name in self.cfg.inbox_keys:
                    return Principal(source=self.cfg.inbox_keys[key.name], client_id=f"key:{key.name}", kind="inbox_key", scopes=())
                return Principal(source=key.name, client_id=f"key:{key.name}", kind="key", **self._limits(key.name), credential=self._key_grant(key.secret))
        row = self.connections.verify(token) if self.connections is not None else None
        if row is not None:
            return Principal(source=row["agent"], client_id=f"connect:{row['id']}", kind="connect",
                             memory_grants=self.connections.memory_scopes(row["id"]), **self._limits(row["agent"]))
        if not self.cfg.resource:
            return None
        row = self.store.one(
            "SELECT * FROM oauth_tokens WHERE token_hash = ? AND kind = 'access' AND revoked_at IS NULL", (sha(token),)
        )
        if row is None or row["expires_at"] < self.store.clock() or row["resource"] not in self.cfg.resources:
            return None
        return Principal(source=row["source"], client_id=row["client_id"], kind="oauth", **self._limits(row["source"]), credential=row["family"])

    def grant_active(self, *, source: str, kind: str, client_id: str, credential: str | None) -> bool:
        """Recheck a long-lived grant without retaining a bearer token."""
        if kind == "key":
            return (client_id == f"key:{source}" and source not in self.cfg.inbox_keys and credential is not None
                    and any(k.name == source and hmac.compare_digest(self._key_grant(k.secret), credential) for k in self.cfg.static_keys))
        if kind == "connect":
            return client_id.startswith("connect:") and self.store.one(
                "SELECT 1 FROM agent_keys WHERE id = ? AND agent = ? AND revoked_at IS NULL",
                (client_id.removeprefix("connect:"), source),
            ) is not None
        if kind != "oauth" or not self.cfg.resource or credential is None:
            return False
        if client_id.startswith("https://"):
            if not host_allowed(urlparse(client_id).hostname, self.cimd_hosts):
                return False
        elif self.store.one("SELECT 1 FROM oauth_clients WHERE client_id = ?", (client_id,)) is None:
            return False
        rows = self.store.q(
            "SELECT resource FROM oauth_tokens WHERE family = ? AND client_id = ? AND source = ? AND expires_at >= ? AND revoked_at IS NULL",
            (credential, client_id, source, self.store.clock()),
        )
        return any(row["resource"] in self.cfg.resources for row in rows)

    def challenge_header(self, error: str | None = None) -> str:
        parts = []
        if self.cfg.issuer:
            parts.append(f'resource_metadata="{self.cfg.issuer}/.well-known/oauth-protected-resource"')
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
            "resource_name": "SwB",
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
        bad = [u for u in uris if not redirect_allowed(u, self.redirect_allow)]
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
        known_native = not self.cfg.cimd_hosts and self.agents.match_cimd(url)
        if u.scheme != "https" or not u.path or u.path == "/" or u.fragment or not (known_native or host_allowed(u.hostname, self.cimd_hosts)):
            raise OAuthError("invalid_client", f"client metadata document host {urlparse(url).hostname} is not allowed (auth.cimd_hosts)", 401)
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
        supported = doc.get("token_endpoint_auth_methods_supported")
        if method != "none" and not (isinstance(supported, list) and "none" in supported):
            raise OAuthError("invalid_client", "this CIMD client must also support token_endpoint_auth_method none", 401)
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
        for base in self.cfg.public_urls:
            if r in (base, f"{base}/mcp"):
                return f"{base}/mcp"
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
        if not assertion or not self.cfg.access_configured:
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

    async def owner(self, request: Request) -> Owner | None:
        email = await self.access_email(request)
        if email and any(hmac.compare_digest(email.encode(), e.encode()) for e in self.cfg.owner_emails):
            return Owner(email, "access")
        return self.sessions.owner(request.cookies.get(SESSION_COOKIE))

    def origin_ok(self, request: Request) -> bool:
        origins = request.headers.getlist("origin")
        if not origins:
            return True
        origin = self.cfg.public_origin
        return origin is not None and all(hmac.compare_digest(value.encode(), origin.encode()) for value in origins)

    def csrf(self, key: str) -> str:
        return hmac.digest(self.secret, f"switchboard-csrf:{key}".encode(), "sha256").hex()

    async def valid_csrf(self, request: Request, key: str) -> bool:
        if not self.origin_ok(request):
            return False
        expected = self.csrf(key).encode()
        header = request.headers.get("x-csrf-token")
        if header is not None and hmac.compare_digest(header.encode(), expected):
            return True
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() in (
            "application/x-www-form-urlencoded", "multipart/form-data",
        ):
            token = (await request.form()).get("csrf")
            return isinstance(token, str) and hmac.compare_digest(token.encode(), expected)
        return False

    def csrf_refused(self, request: Request, *, target: str | None = None) -> JSONResponse:
        if target is None and request.url.path in CSRF_AUDIT_PATHS:
            target = request.url.path
        if self.audit is not None:
            self.audit.record_refusal(origin="http", client=audit_client_ip(request.scope), destination="core",
                                      action="auth_failure", target=target, detail="csrf")
        return JSONResponse({"error": "csrf"}, status_code=403)

    async def protect_owner(self, request: Request) -> tuple[Owner | None, JSONResponse | None]:
        write = request.method not in ("GET", "HEAD")
        if write and not self.origin_ok(request):
            return None, self.csrf_refused(request)
        owner = await self.owner(request)
        if owner is None:
            return None, JSONResponse({"error": "signed_out"}, status_code=401)
        if write and not await self.valid_csrf(request, owner.csrf_key):
            return None, self.csrf_refused(request)
        return owner, None

    async def validate_authorize(self, p: dict[str, str]) -> tuple[Client, dict[str, str]]:
        """Checks that must pass before anything may be redirected to the client."""
        if not self.cfg.issuer:
            raise OAuthError("temporarily_unavailable", "public_url is not configured", 503)
        c = await self.client(p.get("client_id"))
        redirect_uri = p.get("redirect_uri") or (c.redirect_uris[0] if len(c.redirect_uris) == 1 else "")
        if not redirect_uri or not redirect_matches(redirect_uri, c.redirect_uris):
            raise OAuthError("invalid_request", f"redirect_uri {redirect_uri or '(none)'} is not registered for this client")
        if not redirect_allowed(redirect_uri, self.redirect_allow):
            raise OAuthError("invalid_request", f"redirect_uri {redirect_uri} is not allowed by this server (auth.redirect_uris)")
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
        if state is not None:
            q["state"] = state
        return RedirectResponse(_append(redirect_uri, q), status_code=302)

    def issue_code(self, c: Client, p: dict[str, str]) -> str:
        code = secrets.token_urlsafe(32)
        source = source_for(p["redirect_uri"], c, self.cfg.source_names, self.agents)
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
            pages.PageRoute("/authorize", self.h_authorize, pages.AUTHORIZE_TITLE, methods=["GET", "POST"]),
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
            self.failed(request, "/register", e)
            return e.response()
        log.info("registered oauth client %s (%s)", out["client_id"], out.get("client_name") or "unnamed")
        return JSONResponse(out, status_code=201, headers={"Cache-Control": "no-store"})

    async def h_authorize(self, request: Request) -> Response:
        if (d := self._disabled()) is not None:
            return d
        owner = None
        if request.method == "POST":
            owner, refused = await self.protect_owner(request)
            if refused is not None:
                return refused
            form = await request.form()
            try:
                params = self.unsign(str(form.get("req") or ""))
            except OAuthError as e:
                self.failed(request, "/authorize", e)
                return pages.message_page("Sign-in failed", e.description, 400)
        else:
            params = dict(request.query_params)
        try:
            c, params = await self.validate_authorize(params)
        except OAuthError as e:
            self.failed(request, "/authorize", e, params.get("client_id"))
            return pages.message_page("Sign-in failed", e.description, e.status if e.status != 401 else 400)
        state = params.get("state")
        try:
            self.check_request(params)
        except OAuthError as e:
            return self.error_redirect(params["redirect_uri"], state, e)

        if owner is None and (owner := await self.owner(request)) is None:
            return login_redirect(request)

        if request.method == "POST":
            if form.get("decision") == "deny":
                return self.error_redirect(params["redirect_uri"], state, OAuthError("access_denied", "the owner denied the request"))
            if form.get("decision") == "approve":
                code = self.issue_code(c, params)
                log.info("owner approved oauth client %s", c.client_id[:60])
                q = {"code": code, "iss": self.cfg.issuer or ""}
                if state:
                    q["state"] = state
                return RedirectResponse(_append(params["redirect_uri"], q), status_code=302)

        source = source_for(params["redirect_uri"], c, self.cfg.source_names, self.agents)
        who = html.escape((c.name or "").strip() or self.agents.label(source, "") or "OAuth client")
        address = f'<span class="item-line">Mailbox address <code>agent:{html.escape(source)}</code></span>' if source not in self.agents else ""
        dest = html.escape(urlparse(params["redirect_uri"]).netloc)
        owner_name = owner.label if owner.via == "access" else (
            self.cfg.owner_emails[0] if len(self.cfg.owner_emails) == 1 else "owner"
        )
        signed = f'<p class="item-line">Signed in as {html.escape(owner_name)}</p>'
        body = pages.hero("Connect to SwB", "This connection can read and write shared memory and route work.") + f"""
<form method="post" class="fade-in">
<input type="hidden" name="req" value="{html.escape(self.sign(params))}">
<input type="hidden" name="csrf" value="{self.csrf(owner.csrf_key)}">
<div class="card"><div class="item"><div class="item-main"><span class="card-title">{who}</span>{address}<span class="item-line">Returns to <code>{dest}</code></span>{signed}</div></div></div>
<div class="actions"><button class="pill-button primary" name="decision" value="approve">Approve</button>
<button class="text-button" name="decision" value="deny" formnovalidate>Deny</button></div>
</form>"""
        return pages.page(pages.AUTHORIZE_TITLE, body, 200)

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
            self.failed(request, "/token", e)
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
    parts = urlparse(uri)
    query = [part for part in parts.query.split("&") if part and unquote_plus(part.partition("=")[0]) not in q]
    return parts._replace(query="&".join([*query, urlencode(q)])).geturl()
