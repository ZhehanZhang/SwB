"""Following Capy threads over Server-Sent Events, resuming from the last transcript cursor after any drop."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx

log = logging.getLogger("switchboard.capy.stream")


@dataclass
class Frame:
    event: str
    data: dict[str, Any]
    id: str | None = None


class StreamClosed(Exception):
    """The stream ended for good: fatal means Capy refused it (auth, access, a bad cursor), otherwise reconnects ran out."""

    def __init__(self, reason: str, fatal: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.fatal = fatal


async def parse_sse(lines: AsyncIterator[str]) -> AsyncIterator[Frame]:
    event, data, fid = "message", [], None
    async for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if data:
                try:
                    payload = json.loads("\n".join(data))
                except ValueError:
                    payload = {"raw": "\n".join(data)}
                yield Frame(event=event, data=payload if isinstance(payload, dict) else {"value": payload}, id=fid)
            event, data, fid = "message", [], None
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)
        elif name == "id":
            fid = value


class Backoff:
    """Exponential backoff with jitter, reset whenever a frame arrives."""

    def __init__(self, base: float = 1.0, cap: float = 60.0, rand: Callable[[], float] = random.random):
        self.base, self.cap, self.rand, self.attempt = base, cap, rand, 0

    def reset(self) -> None:
        self.attempt = 0

    def next(self) -> float:
        delay = min(self.cap, self.base * (2 ** self.attempt))
        self.attempt += 1
        return delay * (0.5 + self.rand() / 2)


class ThreadStream:
    """One reconnecting reader of GET /threads/{id}/stream. on_frame runs for every frame; cursor() says where to resume."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        thread_id: str,
        on_frame: Callable[[Frame], Awaitable[bool | None]],
        cursor: Callable[[], str | None],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        backoff: Backoff | None = None,
        max_failures: int = 30,
    ):
        self.client = client
        self.thread_id = thread_id
        self.on_frame = on_frame
        self.cursor = cursor
        self.sleep = sleep
        self.backoff = backoff or Backoff()
        self.max_failures = max_failures
        self.connects = 0
        self.connected = False
        self.last_error: str | None = None

    async def run(self) -> str:
        """Read until the server sends done or on_frame returns True. Raises StreamClosed when it has to give up."""
        failures = 0
        while True:
            headers = {"Accept": "text/event-stream"}
            cur = self.cursor()
            if cur:
                headers["Last-Event-ID"] = cur
            delay: float | None = None
            try:
                self.connects += 1
                async with self.client.stream("GET", f"threads/{self.thread_id}/stream", headers=headers,
                                              timeout=httpx.Timeout(10.0, read=60.0)) as r:
                    if r.status_code in (429, 503):
                        delay = float(r.headers.get("retry-after") or 0) or None
                        raise httpx.HTTPStatusError(str(r.status_code), request=r.request, response=r)
                    if r.status_code in (401, 403, 404):
                        raise StreamClosed(f"Capy answered {r.status_code} for the thread stream", fatal=True)
                    if r.status_code == 400 and cur:
                        raise StreamClosed(f"Capy rejected the resume cursor {cur}", fatal=True)
                    r.raise_for_status()
                    self.connected = True
                    async for frame in parse_sse(r.aiter_lines()):
                        failures = 0
                        self.backoff.reset()
                        if frame.event == "done":
                            return "done"
                        if frame.event == "error":
                            code = str(frame.data.get("code") or "")
                            if code in ("unauthorized", "forbidden"):
                                raise StreamClosed(f"stream error: {code}", fatal=True)
                            self.last_error = f"stream error: {code or 'internal'}"
                            break
                        if await self.on_frame(frame):
                            return "finished"
            except StreamClosed:
                raise
            except (httpx.HTTPError, OSError) as e:
                self.last_error = f"{type(e).__name__}: {e}"[:200]
            finally:
                self.connected = False
            failures += 1
            if failures > self.max_failures:
                raise StreamClosed(f"gave up after {failures - 1} failed reconnects ({self.last_error})")
            wait = delay if delay is not None else self.backoff.next()
            log.info("thread %s stream dropped (%s); reconnecting in %.1fs", self.thread_id, self.last_error or "closed", wait)
            await self.sleep(wait)
