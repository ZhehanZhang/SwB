"""Discord REST calls, including webhook requests whose URL contains a secret."""

from __future__ import annotations

import asyncio
import math
from typing import Any

import httpx

from .. import BlockError
from ... import __version__

API = "https://discord.com/api/v10"
USER_AGENT = f"DiscordBot (https://example.test, {__version__}) switchboard"
TIMEOUT = httpx.Timeout(20).as_dict()
ATTEMPTS, MAX_RETRY_AFTER_S = 4, 30.0


class DiscordHTTPError(BlockError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Discord REST returned HTTP {status}")


async def request(transport: httpx.AsyncBaseTransport, method: str, path: str, token: str | None,
                  *, json: dict[str, Any] | None = None, params: dict[str, str] | None = None, retry: bool = True) -> Any:
    """Use the transport directly so the HTTP client never logs credential-bearing webhook URLs."""
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bot {token}"
    attempts = ATTEMPTS if retry else 1
    for attempt in range(attempts):
        try:
            req = httpx.Request(method, API + path, json=json, params=params, extensions={"timeout": TIMEOUT}, headers=headers)
            response = await transport.handle_async_request(req)
            try:
                if response.is_success:
                    return response.json() if await response.aread() else None
                if response.status_code != 429 or attempt == attempts - 1:
                    raise DiscordHTTPError(response.status_code)
                try:
                    await response.aread()
                    delay = float(response.json().get("retry_after"))
                except (ValueError, TypeError, AttributeError):
                    try:
                        delay = float(response.headers.get("Retry-After", "1"))
                    except ValueError:
                        raise DiscordHTTPError(429) from None
                if not math.isfinite(delay) or not 0 <= delay <= MAX_RETRY_AFTER_S:
                    raise DiscordHTTPError(429)
            finally:
                await response.aclose()
        except DiscordHTTPError:
            raise
        except Exception:
            raise BlockError("Discord REST request failed") from None
        await asyncio.sleep(delay)
