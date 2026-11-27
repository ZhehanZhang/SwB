from __future__ import annotations

import json
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from .caps.base import CompanionContext

STATES = ("working", "blocked", "idle", "done", "ended", "unknown")
WAKE_TTL_S = 1800
REPLY_MAX = 12_000
COALESCE_S = 1.0


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
        out["dir"] = self.dir.rsplit("/", 1)[-1][:64]
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

    def expect(self, keys: Iterable[str], session: str, conversation: str, agent: str) -> None:
        now = time.monotonic()
        for key in [key for key, wake in self.wakes.items() if wake.expires <= now]:
            del self.wakes[key]
        wake = Wake(session, conversation, agent, now + WAKE_TTL_S)
        for key in keys:
            if key:
                self.wakes[key] = wake

    def pending(self, keys: Iterable[str]) -> Wake | None:
        now = time.monotonic()
        for key in keys:
            if key and (wake := self.wakes.get(key)):
                if wake.expires > now:
                    return wake
                del self.wakes[key]
        return None

    def take(self, keys: Iterable[str]) -> Wake | None:
        if wake := self.pending(keys):
            for key in [key for key, value in self.wakes.items() if value is wake]:
                del self.wakes[key]
        return wake

    async def report(self, ctx: CompanionContext, cap: str, session: Session) -> bool:
        payload = session.payload()
        key = f"{session.harness}:{session.session}"
        last = self.reported.get(key)
        now = time.monotonic()
        if last and last[1] == payload:
            return False
        if last and now - last[0] < COALESCE_S and payload["state"] == last[1]["state"]:
            return False
        await ctx.emit(f"{cap}.session", payload)
        if payload["state"] == "ended":
            self.reported.pop(key, None)
        else:
            self.reported[key] = (now, payload)
        return True

    async def reply(self, ctx: CompanionContext, cap: str, keys: Iterable[str], text: str) -> bool:
        if not text.strip() or not (wake := self.take(keys)):
            return False
        text = text[:REPLY_MAX].encode("utf-8", errors="replace").decode("utf-8")
        if len(json.dumps(text, ensure_ascii=False).encode()) > REPLY_MAX + 2:
            lower, upper = 0, len(text)
            while lower < upper:
                middle = (lower + upper + 1) // 2
                if len(json.dumps(text[:middle], ensure_ascii=False).encode()) <= REPLY_MAX + 2:
                    lower = middle
                else:
                    upper = middle - 1
            text = text[:lower]
        await ctx.emit(f"{cap}.reply", {"session": wake.session, "conversation": wake.conversation, "agent": wake.agent,
                                        "text": text})
        return True


_HARNESS = Harness()


def harness() -> Harness:
    return _HARNESS
