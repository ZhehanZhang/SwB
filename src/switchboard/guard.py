"""Cheap protection in front of everything: request size limits, per-IP rate limits, and lockout after failed bearer keys.

None of it changes what Claude, ChatGPT or Capy see when they behave: the limits sit far above normal use.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict, deque
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address
from typing import Any, Callable

from starlette.exceptions import HTTPException
from starlette.datastructures import Headers, MutableHeaders

from .bot_identity import MAX_IDENTITY_BODY
from .companion.memory import MEMORY_FRAME_LIMIT, MEMORY_SYNC_EVENT
from .memory_archive import MAX_ARCHIVE_BYTES

BODY_LIMIT = 1 * 1024 * 1024
MCP_BODY_LIMIT = 16 * 1024 * 1024
COMPANION_EMIT_LIMIT = 16 * 1024
SETUP_BODY_LIMIT = 1024
INVALID_FORWARDED = "invalid-forwarded"
TRUSTED_ONLY_FORWARDED = "trusted-only-forwarded"
LIMITS = {
    "register": (20, 3600.0),
    "token": (120, 600.0),
    "authorize": (60, 600.0),
    "bearer_fail": (20, 600.0),
    "connect_fail": (10, 600.0),
    "companion_pair_fail": (10, 600.0),
    "companion_bearer_fail": (20, 600.0),
    "login_link_fail": (10, 600.0),
    "owner_setup": (8, 600.0),
    "owner_setup_total": (120, 600.0),
    "passkey_options": (60, 600.0),
    "passkey_fail": (10, 600.0),
    "sign_in_refusal_audit": (1, 60.0),
}


def _ip(value: str) -> IPv4Address | IPv6Address | None:
    try:
        return ip_address(value.strip()) if "%" not in value else None
    except ValueError:
        return None


def _peer_ip(scope: dict[str, Any]) -> IPv4Address | IPv6Address | None:
    client = scope.get("client")
    return _ip(str(client[0])) if client else None


def _resolved_ip(scope: dict[str, Any], trusted_proxies: tuple[IPv4Network | IPv6Network, ...]) -> str:
    peer = _peer_ip(scope)
    if peer is None:
        return "-"
    if not any(peer in network for network in trusted_proxies):
        return str(peer)
    headers = Headers(scope=scope)
    forwarded = headers.getlist("x-forwarded-for")
    if forwarded:
        values = (value for field in reversed(forwarded) for value in reversed(field.split(",")))
    else:
        cf = headers.getlist("cf-connecting-ip")
        if not cf:
            return str(peer)
        if len(cf) != 1:
            return INVALID_FORWARDED
        values = iter(cf)
    junk = False
    for value in values:
        address = _ip(value)
        if address is None:
            junk = True
        elif not any(address in network for network in trusted_proxies):
            return str(address)
    return INVALID_FORWARDED if junk else TRUSTED_ONLY_FORWARDED


def client_ip(scope: dict[str, Any]) -> str:
    if resolved := scope.get("swb.client_ip"):
        return resolved
    return str(peer) if (peer := _peer_ip(scope)) is not None else "-"


audit_client_ip = client_ip


class RateLimiter:
    """In-memory sliding windows. Each bucket expires idle keys and evicts the least recently hit key at capacity."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, limits: dict[str, tuple[int, float]] | None = None,
                 *, max_keys_per_bucket: int = 5_000):
        self.clock = clock
        self.limits = limits or LIMITS
        self.max_keys_per_bucket = max_keys_per_bucket
        self.hits: dict[str, OrderedDict[str, deque[float]]] = {}
        self._last_now = float("-inf")

    def _now(self) -> float:
        self._last_now = max(self._last_now, self.clock())
        return self._last_now

    @staticmethod
    def _expire(peers: OrderedDict[str, deque[float]], cutoff: float) -> None:
        while peers and next(iter(peers.values()))[-1] <= cutoff:
            peers.popitem(last=False)

    def blocked(self, bucket: str, key: str) -> float | None:
        """Seconds until the next attempt is allowed, or None when it may go ahead."""
        n, span = self.limits[bucket]
        if not (peers := self.hits.get(bucket)):
            return None
        now = self._now()
        cutoff = now - span
        self._expire(peers, cutoff)
        if (q := peers.get(key)) is None:
            return None
        while q[0] <= cutoff:
            q.popleft()
        return max(1.0, q[0] + span - now) if len(q) >= n else None

    def hit(self, bucket: str, key: str) -> None:
        n, span = self.limits[bucket]
        if n <= 0:
            return
        now = self._now()
        peers = self.hits.setdefault(bucket, OrderedDict())
        self._expire(peers, now - span)
        if (q := peers.get(key)) is None:
            if len(peers) >= self.max_keys_per_bucket:
                peers.popitem(last=False)
            peers[key] = q = deque(maxlen=n)
        q.append(now)
        peers.move_to_end(key)


async def reject(send: Any, status: int, error: str, description: str, retry_after: float | None = None) -> None:
    headers = [(b"content-type", b"application/json")]
    if retry_after is not None:
        headers.append((b"retry-after", str(int(retry_after + 0.999)).encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": json.dumps({"error": error, "error_description": description}).encode()})


class Guard:
    """ASGI middleware: body size limits everywhere, and rate limits on /register, /token and /authorize."""

    PATHS = {"/register": "register", "/token": "token", "/authorize": "authorize"}

    def __init__(self, app: Any, limiter: RateLimiter, on_refusal: Callable[[str, str, str], None] | None = None,
                 *, trusted_proxies: tuple[IPv4Network | IPv6Network, ...] = (),
                 on_large_body: Callable[[dict], None] | None = None):
        self.app = app
        self.limiter = limiter
        self.on_refusal = on_refusal or (lambda ip, path, why: None)
        self.on_large_body = on_large_body or (lambda scope: None)
        self.trusted_proxies = trusted_proxies

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        scope["swb.client_ip"] = _resolved_ip(scope, self.trusted_proxies)
        path = scope["path"]
        if path == "/login" or path.startswith("/auth/"):
            original_send = send

            async def send(message: dict) -> None:
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(scope=message)
                    headers["Cache-Control"] = "no-store"
                    headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
                    headers.setdefault("Referrer-Policy", "same-origin")
                await original_send(message)

        limit = MCP_BODY_LIMIT if path == "/mcp" or path.startswith("/mcp/") or path.rstrip("/") == "/companion/results" else BODY_LIMIT
        if path.rstrip("/") == "/companion/emit":
            event_limit = MEMORY_FRAME_LIMIT if scope.get("query_string") == f"kind={MEMORY_SYNC_EVENT}".encode() else COMPANION_EMIT_LIMIT
            limit = event_limit + 256
        elif path == "/auth/setup":
            limit = SETUP_BODY_LIMIT
        elif path == "/api/memory/import" and scope.get("method") == "POST":
            limit = MAX_ARCHIVE_BYTES + 64 * 1024
        elif path.startswith("/api/bot-identities/") and scope.get("method") == "POST":
            limit = MAX_IDENTITY_BODY
        for k, v in scope.get("headers", []):
            if k == b"content-length":
                try:
                    too_big = int(v) > limit
                except ValueError:
                    too_big = True
                if too_big:
                    self.on_large_body(scope)
                    await reject(send, 413, "request_too_large", f"request bodies are limited to {limit} bytes")
                    return
        bucket = self.PATHS.get(path)
        if bucket and (scope.get("method") == "POST" or bucket == "authorize"):
            ip = client_ip(scope)
            wait = self.limiter.blocked(bucket, ip)
            if wait is not None:
                self.on_refusal(ip, path, f"{bucket} rate limit")
                await reject(send, 429, "slow_down", "too many requests; try again later", wait)
                return
            self.limiter.hit(bucket, ip)
        seen = 0

        async def counted() -> Any:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    self.on_large_body(scope)
                    raise HTTPException(413, "request body too large")
            return message

        await self.app(scope, counted, send)
