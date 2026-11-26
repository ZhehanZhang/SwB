"""Telegram's Bot API. Its credential is in the URL, so requests bypass httpx's client-level URL logging."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .. import BlockError


class TelegramError(BlockError):
    def __init__(self, method: str, reason: str, *, code: int | None = None, retry_after: int | None = None):
        super().__init__(f"Telegram {method}: {reason}")
        self.code, self.retry_after = code, retry_after


class BotAPI:
    def __init__(self, transport: httpx.AsyncBaseTransport | None):
        self._transport = transport if transport is not None else httpx.AsyncHTTPTransport(trust_env=False)

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def _request(self, token: str, method: str, params: dict[str, Any]) -> Any:
        timeout = httpx.Timeout(10, read=60 if method == "getUpdates" else 10)
        request = httpx.Request("POST", f"https://api.telegram.org/bot{token}/{method}", json=params,
                                extensions={"timeout": timeout.as_dict()})
        try:
            response = await self._transport.handle_async_request(request)
            try:
                await response.aread()
            finally:
                await response.aclose()
        except httpx.HTTPError:
            response = None
        if response is None:
            raise TelegramError(method, "network request failed")

        try:
            data = response.json()
        except ValueError:
            data = None
        status = response.status_code
        if not isinstance(data, dict):
            data = {}
        if 200 <= status < 300 and data.get("ok") is True and "result" in data:
            return data["result"]
        code = data.get("error_code")
        code = code if type(code) is int and 400 <= code <= 599 and status not in (401, 404, 429) else status
        details = data.get("parameters")
        delay = details.get("retry_after") if isinstance(details, dict) else None
        if 400 <= code <= 599:
            raise TelegramError(method, f"API error {code}", code=code,
                                retry_after=delay if code == 429 and type(delay) is int and delay >= 0 else None)
        raise TelegramError(method, "invalid API response")

    async def call(self, token: str, method: str, *, retry: bool = True, **params: Any) -> Any:
        try:
            return await self._request(token, method, params)
        except TelegramError as exc:
            if not retry or exc.code != 429:
                raise
            delay = exc.retry_after
        await asyncio.sleep(delay if delay is not None else 1)
        return await self._request(token, method, params)
