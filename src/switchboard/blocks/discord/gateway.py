"""Discord Gateway v10 connection and session lifecycle."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import sys
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

if TYPE_CHECKING:
    from . import DiscordBlock

log = logging.getLogger("switchboard.discord.gateway")

_DISPATCH = 0
_HEARTBEAT = 1
_IDENTIFY = 2
_RESUME = 6
_RECONNECT = 7
_INVALID_SESSION = 9
_HELLO = 10
_HEARTBEAT_ACK = 11

_GATEWAY_HOST = re.compile(r"gateway(?:-[a-z0-9]+)*\.discord\.gg")
_FATAL_CODES = frozenset({4004, 4010, 4011, 4012, 4013, 4014})


class _Reconnect(Exception):
    def __init__(self, *, code: int | None = None, delay: float | None = None, reset: bool = False):
        self.code = code
        self.delay = delay
        self.reset = reset


def _gateway_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        host = url.hostname
        if (
            url.scheme != "wss"
            or not host
            or not _GATEWAY_HOST.fullmatch(host)
            or url.username is not None
            or url.port not in (None, 443)
            or url.fragment
        ):
            return None
    except ValueError:
        return None
    return urlunsplit(("wss", host, url.path or "/", "v=10&encoding=json", ""))


async def _reject_redirect(*args: Any) -> None:
    raise aiohttp.ClientConnectionError("Discord Gateway redirects are not allowed")


@asynccontextmanager
async def _connect(url: str) -> AsyncIterator[aiohttp.ClientWebSocketResponse]:
    trace = aiohttp.TraceConfig()
    trace.on_request_redirect.append(_reject_redirect)
    async with (
        aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), trace_configs=[trace]) as session,
        session.ws_connect(url) as socket,
    ):
        yield socket


async def _receive(socket: Any) -> dict[str, Any]:
    message = await socket.receive()
    if message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
        code = message.data if message.type == aiohttp.WSMsgType.CLOSE else None
        if not isinstance(code, int):
            code = getattr(socket, "close_code", None)
        raise _Reconnect(code=code if isinstance(code, int) else None)
    if message.type != aiohttp.WSMsgType.TEXT:
        return {}
    try:
        payload = json.loads(message.data)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    op = payload.get("op")
    if op == _RECONNECT:
        raise _Reconnect(delay=0)
    if op == _INVALID_SESSION:
        raise _Reconnect(delay=random.uniform(1, 5), reset=payload.get("d") is not True)
    return payload


class Gateway:
    def __init__(
        self,
        block: DiscordBlock,
        connect: Callable[[str], AbstractAsyncContextManager[Any]] | None = None,
    ):
        self.block = block
        self.connect = connect if connect is not None else _connect
        self.ready = False
        self.fatal_code: int | None = None
        self._seq: int | None = None
        self._session_id: str | None = None
        self._resume_url: str | None = None

    def _clear_session(self) -> None:
        self._seq = None
        self._session_id = None
        self._resume_url = None

    async def run(self, token: str) -> None:
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=1024)
        worker = asyncio.create_task(self._dispatch(queue), name="discord-gateway-dispatch")
        try:
            url = None
            backoff = 1.0
            while self.fatal_code is None:
                delay = None
                try:
                    if url is None:
                        try:
                            discovery = await self.block.request("GET", "/gateway/bot")
                        except Exception as exc:
                            if getattr(exc, "status", None) == 401:
                                raise _Reconnect(code=4004) from None
                            raise
                        url = _gateway_url(discovery.get("url")) if isinstance(discovery, dict) else None
                        if url is None:
                            log.warning("Discord Gateway discovery did not return a valid endpoint")
                            raise _Reconnect()
                    async with self.connect(self._resume_url or url) as socket:
                        try:
                            await self._listen(socket, token, queue)
                        finally:
                            if self.ready:
                                backoff = 1.0
                            self.ready = False
                            with suppress(Exception):
                                async with asyncio.timeout(2):
                                    await socket.close(code=4000)
                except _Reconnect as exc:
                    if exc.code in _FATAL_CODES:
                        self.fatal_code = exc.code
                        return
                    if exc.reset or exc.code in (4007, 4009):
                        self._clear_session()
                    delay = exc.delay
                except Exception:
                    log.warning("Discord Gateway connection failed; retrying")
                if delay is None:
                    delay = random.uniform(backoff / 2, backoff)
                    backoff = min(60.0, backoff * 2)
                await asyncio.sleep(delay)
        finally:
            self.ready = False
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def _listen(
        self,
        socket: Any,
        token: str,
        queue: asyncio.Queue[tuple[str, dict[str, Any]]],
    ) -> None:
        async with asyncio.timeout(15):
            hello = await _receive(socket)
        data = hello.get("d")
        interval_ms = data.get("heartbeat_interval") if isinstance(data, dict) else None
        if (
            hello.get("op") != _HELLO
            or type(interval_ms) not in (int, float)
            or not math.isfinite(interval_ms)
            or interval_ms <= 0
        ):
            raise _Reconnect()

        loop = asyncio.get_running_loop()
        interval = interval_ms / 1000
        next_heartbeat = loop.time() + random.random() * interval
        pending: deque[float] = deque()

        async def heartbeat() -> float:
            deadline = loop.time() + interval
            pending.append(deadline)
            async with asyncio.timeout(10):
                await socket.send_json({"op": _HEARTBEAT, "d": self._seq})
            return deadline

        async with asyncio.timeout(10):
            if self._session_id is not None and self._seq is not None:
                await socket.send_json({
                    "op": _RESUME,
                    "d": {"token": token, "session_id": self._session_id, "seq": self._seq},
                })
            else:
                self._clear_session()
                await socket.send_json({
                    "op": _IDENTIFY,
                    "d": {
                        "token": token,
                        "intents": self.block.intents,
                        "properties": {"os": sys.platform, "browser": "swb", "device": "swb"},
                    },
                })

        while True:
            now = loop.time()
            if pending and pending[0] <= now:
                raise _Reconnect()
            if next_heartbeat <= now:
                next_heartbeat = await heartbeat()
                continue

            deadline = min(next_heartbeat, pending[0]) if pending else next_heartbeat
            receive_timeout = asyncio.timeout_at(deadline)
            try:
                async with receive_timeout:
                    payload = await _receive(socket)
            except TimeoutError:
                if not receive_timeout.expired():
                    raise
                continue
            op = payload.get("op")
            data = payload.get("d")
            if op == _HEARTBEAT:
                await heartbeat()
            elif op == _HEARTBEAT_ACK:
                if pending:
                    pending.popleft()
            elif op == _DISPATCH:
                event = payload.get("t")
                if isinstance(event, str) and isinstance(data, dict):
                    try:
                        queue.put_nowait((event, data))
                    except asyncio.QueueFull:
                        log.warning("Discord Gateway dispatch queue is full; reconnecting to replay events")
                        raise _Reconnect() from None
                    if event == "READY":
                        session_id = data.get("session_id")
                        self._session_id = session_id if isinstance(session_id, str) and session_id else None
                        self._resume_url = _gateway_url(data.get("resume_gateway_url")) if self._session_id else None
                    if event in ("READY", "RESUMED"):
                        self.ready = True
                seq = payload.get("s")
                if type(seq) is int:
                    self._seq = seq

    async def _dispatch(self, queue: asyncio.Queue[tuple[str, dict[str, Any]]]) -> None:
        while True:
            event, data = await queue.get()
            try:
                await self.block.dispatch(event, data)
            except Exception:
                log.warning("Discord Gateway event dispatch failed")
