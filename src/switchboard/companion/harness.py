from __future__ import annotations

import json
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .caps.base import CompanionContext

STATES = ("working", "blocked", "idle", "done", "ended", "unknown")
WAKE_TTL_S = 1800
REPLY_MAX = 12_000
COALESCE_S = 1.0


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

    def reset_reports(self) -> None:
        self.reported.clear()

    def expect(self, keys: Iterable[str], session: str, conversation: str, agent: str) -> None:
        wake = Wake(session, conversation, agent, time.monotonic() + WAKE_TTL_S)
        for key in keys:
            if key:
                self.wakes[key] = wake

    def take(
        self, keys: Iterable[str], *, received_at: float | None = None, expected_agent: str | None = None,
    ) -> Wake | None:
        now = time.monotonic()
        for key in [k for k in keys if k]:
            wake = self.wakes.get(key)
            if (wake and wake.expires > now
                    and (received_at is None or wake.expires - WAKE_TTL_S <= received_at)
                    and (expected_agent is None or wake.agent == expected_agent)):
                for k in [k for k, w in self.wakes.items() if w is wake]:
                    del self.wakes[k]
                return wake
        return None

    async def report(self, ctx: CompanionContext, cap: str, session: Session) -> bool:
        payload = session.payload()
        key = f"{cap}:{session.harness}:{session.session}"
        last = self.reported.get(key)
        now = time.monotonic()
        if last and last[1] == payload:
            return False
        if last and now - last[0] < COALESCE_S and payload["state"] == last[1]["state"]:
            return False
        self.reported[key] = (now, payload)
        if payload["state"] == "ended":
            self.reported.pop(key, None)
        await ctx.emit(f"{cap}.session", payload)
        return True

    async def reply(
        self, ctx: CompanionContext, cap: str, keys: Iterable[str], text: str, *,
        received_at: float | None = None, expected_agent: str | None = None,
    ) -> bool:
        text = reply_text(text)
        if not text.strip() or not (wake := self.take(keys, received_at=received_at, expected_agent=expected_agent)):
            return False
        await ctx.emit(f"{cap}.reply", {"session": wake.session, "conversation": wake.conversation, "agent": wake.agent,
                                        "text": text})
        return True


_HARNESS = Harness()


def harness() -> Harness:
    return _HARNESS
