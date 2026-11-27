"""Cheap protection in front of everything: request size limits, per-IP rate limits, and lockout after failed bearer keys.

None of it changes what Claude, ChatGPT or Capy see when they behave: the limits sit far above normal use.
"""

from __future__ import annotations

import json
import time
from collections import deque
from typing import Any, Callable

from starlette.exceptions import HTTPException
from starlette.datastructures import MutableHeaders

BODY_LIMIT = 1 * 1024 * 1024
MCP_BODY_LIMIT = 16 * 1024 * 1024
SETUP_BODY_LIMIT = 1024
LIMITS = {
    "register": (20, 3600.0),
    "token": (120, 600.0),
    "authorize": (60, 600.0),
    "bearer_fail": (20, 600.0),
    "connect_fail": (10, 600.0),
    "login_link_fail": (10, 600.0),
    "owner_setup": (8, 600.0),
    "owner_setup_total": (120, 600.0),
    "passkey_options": (60, 600.0),
    "passkey_fail": (10, 600.0),
}


def client_ip(scope: dict[str, Any]) -> str:
    for k, v in scope.get("headers", []):
        if k == b"cf-connecting-ip":
            return v.decode()[:64]
    client = scope.get("client")
    return str(client[0]) if client else "?"


class RateLimiter:
    """Sliding-window counters per (bucket, key), in memory: a restart forgets them, which is acceptable for these limits."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, limits: dict[str, tuple[int, float]] | None = None):
        self.clock = clock
        self.limits = limits or LIMITS
        self.hits: dict[tuple[str, str], deque[float]] = {}

    def _window(self, bucket: str, key: str) -> deque[float]:
        n, span = self.limits[bucket]
        q = self.hits.setdefault((bucket, key), deque())
        cutoff = self.clock() - span
        while q and q[0] < cutoff:
            q.popleft()
        if not q:
            self.hits.pop((bucket, key), None)
            q = self.hits.setdefault((bucket, key), deque())
        return q

    def blocked(self, bucket: str, key: str) -> float | None:
        """Seconds until the next attempt is allowed, or None when it may go ahead."""
        n, span = self.limits[bucket]
        q = self._window(bucket, key)
        if len(q) < n:
            return None
        return max(1.0, q[0] + span - self.clock())

    def hit(self, bucket: str, key: str) -> None:
        self._window(bucket, key).append(self.clock())
        if len(self.hits) > 50_000:
            self.hits.clear()


async def reject(send: Any, status: int, error: str, description: str, retry_after: float | None = None) -> None:
    headers = [(b"content-type", b"application/json")]
    if retry_after is not None:
        headers.append((b"retry-after", str(int(retry_after + 0.999)).encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": json.dumps({"error": error, "error_description": description}).encode()})


class Guard:
    """ASGI middleware: body size limits everywhere, and rate limits on /register, /token and /authorize."""

    PATHS = {"/register": "register", "/token": "token", "/authorize": "authorize"}

    def __init__(self, app: Any, limiter: RateLimiter, on_refusal: Callable[[str, str, str], None] | None = None):
        self.app = app
        self.limiter = limiter
        self.on_refusal = on_refusal or (lambda ip, path, why: None)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
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

        limit = MCP_BODY_LIMIT if path == "/mcp" or path.startswith("/mcp/") else BODY_LIMIT
        if path == "/auth/setup":
            limit = SETUP_BODY_LIMIT
        for k, v in scope.get("headers", []):
            if k == b"content-length":
                try:
                    too_big = int(v) > limit
                except ValueError:
                    too_big = True
                if too_big:
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
                    raise HTTPException(413, "request body too large")
            return message

        await self.app(scope, counted, send)
