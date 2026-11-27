from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from .caps.base import CapabilityError

if TYPE_CHECKING:
    from .caps.base import CompanionContext

STATES = ("working", "blocked", "idle", "done", "ended", "unknown")
WAKE_TTL_S = 1800
REPLY_MAX = 12_000
COALESCE_S = 1.0
REFRESH_S = 21600
REPLY_RETRY_MAX_S = 60


def reply_text(text: str) -> str:
    def size(length: int) -> int:
        return len(json.dumps(text[:length], ensure_ascii=False).encode("utf-8")) - 2

    end = min(REPLY_MAX, len(text))
    try:
        if size(end) <= REPLY_MAX:
            return text[:end]
        start = 0
        while start < end:
            middle = (start + end + 1) // 2
            if size(middle) <= REPLY_MAX:
                start = middle
            else:
                end = middle - 1
        return text[:start]
    except UnicodeEncodeError:
        return ""


@dataclass(frozen=True)
class Session:
    harness: str
    session: str
    agent: str = ""
    name: str = ""
    dir: str = ""
    state: str = "unknown"
    detail: str = ""

    def payload(self) -> dict[str, Any]:
        out = asdict(self)
        out["state"] = self.state if self.state in STATES else "unknown"
        out["name"] = self.name[:64]
        out["agent"] = self.agent[:32]
        out["dir"] = self.dir.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1][:64]
        out["detail"] = self.detail[:120]
        return out


@dataclass(frozen=True)
class Wake:
    session: str
    conversation: str
    agent: str
    expires: float


class Harness:
    """Process-wide state shared by the harness capabilities: what was last reported, and which sessions SwB woke."""

    def __init__(self) -> None:
        self.reported: dict[str, tuple[float, dict[str, Any]]] = {}
        self.wakes: dict[str, Wake] = {}
        self._replying: set[int] = set()
        self._retries: set[asyncio.Task[None]] = set()

    def reset_reports(self) -> None:
        self.reported.clear()

    def expect(self, keys: Iterable[str], session: str, conversation: str, agent: str) -> None:
        now = time.monotonic()
        for key in [key for key, wake in self.wakes.items() if wake.expires <= now]:
            del self.wakes[key]
        wake = Wake(session, conversation, agent, now + WAKE_TTL_S)
        for key in keys:
            if key:
                self.wakes[key] = wake

    def pending(
        self, keys: Iterable[str], *, received_at: float | None = None, expected_agent: str | None = None,
    ) -> Wake | None:
        now = time.monotonic()
        for key in keys:
            if not key or not (wake := self.wakes.get(key)):
                continue
            if wake.expires <= now:
                del self.wakes[key]
                continue
            if ((received_at is None or wake.expires - WAKE_TTL_S <= received_at)
                    and (expected_agent is None or wake.agent == expected_agent)):
                return wake
        return None

    def take(
        self, keys: Iterable[str], *, received_at: float | None = None, expected_agent: str | None = None,
    ) -> Wake | None:
        if wake := self.pending(keys, received_at=received_at, expected_agent=expected_agent):
            self._forget(wake)
        return wake

    def _forget(self, wake: Wake) -> None:
        for key in [key for key, value in self.wakes.items() if value is wake]:
            del self.wakes[key]

    def replying(self, wake: Wake) -> bool:
        return id(wake) in self._replying

    async def report(self, ctx: CompanionContext, cap: str, session: Session) -> bool:
        payload = session.payload()
        key = f"{cap}:{session.harness}:{session.session}"
        last = self.reported.get(key)
        now = time.monotonic()
        if last and last[1] == payload and now - last[0] < REFRESH_S:
            return False
        if last and now - last[0] < COALESCE_S and payload["state"] == last[1]["state"]:
            return False
        await ctx.emit(f"{cap}.session", payload)
        if payload["state"] == "ended":
            self.reported.pop(key, None)
        else:
            self.reported[key] = (now, payload)
        return True

    async def reply(
        self, ctx: CompanionContext, cap: str, keys: Iterable[str], text: str, *,
        received_at: float | None = None, expected_agent: str | None = None,
    ) -> bool:
        keys = tuple(keys)
        if (not text.strip() or not (wake := self.pending(keys, received_at=received_at, expected_agent=expected_agent))
                or self.replying(wake)):
            return False
        text = reply_text(text.encode("utf-8", errors="replace").decode("utf-8"))
        kind = f"{cap}.reply"
        payload = {"session": wake.session, "conversation": wake.conversation, "agent": wake.agent, "text": text}
        self._replying.add(id(wake))
        queued = False
        try:
            await ctx.emit(kind, payload)
        except CapabilityError:
            task = asyncio.create_task(self._retry_reply(ctx, kind, payload, keys, wake))
            self._retries.add(task)
            task.add_done_callback(self._retries.discard)
            queued = True
            return False
        finally:
            if not queued:
                self._replying.discard(id(wake))
        self._forget(wake)
        return True

    async def _retry_reply(
        self, ctx: CompanionContext, kind: str, payload: dict[str, Any], keys: tuple[str, ...], wake: Wake,
    ) -> None:
        from .client import Disconnected

        delay = 1
        try:
            while (remaining := wake.expires - time.monotonic()) > 0:
                await asyncio.sleep(min(delay, remaining))
                if time.monotonic() >= wake.expires or not any(self.wakes.get(key) is wake for key in keys):
                    break
                try:
                    await ctx.emit(kind, payload)
                except CapabilityError:
                    delay = min(delay * 2, REPLY_RETRY_MAX_S)
                except (Disconnected, RuntimeError):
                    return
                else:
                    self._forget(wake)
                    return
            if time.monotonic() >= wake.expires:
                self._forget(wake)
        finally:
            self._replying.discard(id(wake))


_HARNESS = Harness()


def harness() -> Harness:
    return _HARNESS
