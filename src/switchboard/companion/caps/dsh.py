from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..harness import STATES, Session, harness
from ..state import state_path
from ._harness import HARNESS_ACTIONS, check_list_args, folder_name, prompt_args, reconnect
from .base import Capability, CapabilityError, CompanionContext, JobContext

_METHODS = frozenset({"list", "prompt", "subscribe"})
_IO_TIMEOUT = 5
_RECONCILE_S = 5
_MAX_LINE = 1 << 20
_SESSION_ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}")
_UNAVAILABLE = "DeepSeek Harness is unavailable on this device."
_REQUEST_FAILED = "DeepSeek Harness could not complete the request."
_ERRORS = {
    "agent_blocked": "Agent is waiting on a prompt.",
    "agent_not_running": "Agent is not running.",
}


def _socket_path() -> Path:
    if os.name == "posix":
        try:
            return state_path().with_name("dsh.sock")
        except RuntimeError:
            pass
    raise CapabilityError(_UNAVAILABLE)


def _session_id(value: Any) -> str:
    return value if isinstance(value, str) and _SESSION_ID.fullmatch(value) and value not in {".", ".."} else ""


def _state(value: Any) -> str:
    return value if value in STATES else "unknown"


async def _read(reader: asyncio.StreamReader) -> dict[str, Any]:
    try:
        line = await reader.readline()
        if line.endswith(b"\n") and isinstance(message := json.loads(line), dict):
            return message
    except (OSError, ValueError, RecursionError):
        pass
    raise CapabilityError(_REQUEST_FAILED)


def _check_error(message: dict[str, Any]) -> None:
    if "error" in message:
        error = message["error"]
        code = error.get("code") if isinstance(error, dict) else None
        raise CapabilityError(_ERRORS.get(code, _REQUEST_FAILED) if isinstance(code, str) else _REQUEST_FAILED)


@asynccontextmanager
async def _exchange(
    path: Path, method: str, **fields: str,
) -> AsyncIterator[tuple[dict[str, Any], asyncio.StreamReader]]:
    if method not in _METHODS:
        raise CapabilityError(_REQUEST_FAILED)
    writer = None
    try:
        try:
            async with asyncio.timeout(_IO_TIMEOUT):
                reader, writer = await asyncio.open_unix_connection(path, limit=_MAX_LINE)
                writer.write(json.dumps({"method": method, **fields}).encode("ascii") + b"\n")
                await writer.drain()
                message = await _read(reader)
        except CapabilityError:
            raise
        except (OSError, ValueError, NotImplementedError):
            raise CapabilityError(_UNAVAILABLE) from None
        _check_error(message)
        result = message.get("result")
        if not isinstance(result, dict):
            raise CapabilityError(_REQUEST_FAILED)
        yield result, reader
    finally:
        if writer is not None:
            writer.close()
            with suppress(OSError):
                await asyncio.wait_for(writer.wait_closed(), _IO_TIMEOUT)


async def _list_sessions(path: Path) -> dict[str, Session]:
    async with _exchange(path, "list") as (result, _):
        rows = result.get("sessions")
        if not isinstance(rows, list):
            raise CapabilityError(_REQUEST_FAILED)
        sessions = {}
        for row in rows:
            if not isinstance(row, dict) or not (session := _session_id(row.get("id"))):
                continue
            state = _state(row.get("state"))
            if state == "ended":
                continue
            name = row.get("name")
            if not isinstance(name, str) or not name.isprintable() or any(char in name for char in "/\\:"):
                name = ""
            sessions[session] = Session("dsh", session, "dsh", name, folder_name(row.get("dir")), state)
        return sessions


@dataclass
class _Delivery:
    finals: dict[str, tuple[CompanionContext, str]] = field(default_factory=dict)
    ended: bool = False


class DshCapability(Capability):
    id = "dsh"
    version = "1"
    title = "DeepSeek Harness"
    actions = HARNESS_ACTIONS

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._prompting: dict[str, _Delivery] = {}
        self._receipts: dict[str, str] = {}

    def available(self) -> str | None:
        if os.name == "posix":
            with suppress(CapabilityError, OSError):
                if shutil.which("dsh") or _socket_path().is_socket():
                    return None
        return _UNAVAILABLE

    async def invoke(self, action: str, args: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
        if action == "list":
            check_list_args(args)
            return {"sessions": [session.payload() for session in (await _list_sessions(_socket_path())).values()]}
        if action != "prompt":
            raise CapabilityError("Choose a DeepSeek Harness action.")
        session, text, conversation, agent = prompt_args(args)
        if not _session_id(session):
            raise CapabilityError("Choose a DeepSeek Harness session ID.")
        keys = [f"{self.id}:{session}"]
        if session in self._prompting or harness().pending(keys):
            raise CapabilityError("The DeepSeek Harness session has a pending wake.")
        delivery = self._prompting[session] = _Delivery()
        try:
            async with _exchange(_socket_path(), "prompt", session=session, text=text) as (result, _):
                receipt = result.get("receipt")
                if not isinstance(receipt, dict) or not (message_id := _session_id(receipt.get("messageId"))):
                    raise CapabilityError(_REQUEST_FAILED)
            final = delivery.finals.get(message_id)
            if final or not delivery.ended:
                harness().expect(keys, session, conversation, agent)
            if final:
                companion, answer = final
                with suppress(CapabilityError):
                    await harness().reply(companion, self.id, keys, answer)
            elif not delivery.ended:
                self._receipts[session] = message_id
        finally:
            del self._prompting[session]
        return {"delivered": True}

    async def _end(self, ctx: CompanionContext, session: str) -> None:
        if delivery := self._prompting.get(session):
            delivery.ended = True
        self._receipts.pop(session, None)
        harness().take([f"{self.id}:{session}"])
        if current := self._sessions.get(session):
            await harness().report(ctx, self.id, replace(current, state="ended"))
            del self._sessions[session]

    async def _reconcile(self, ctx: CompanionContext, path: Path) -> None:
        sessions = await _list_sessions(path)
        for session in self._sessions.keys() - sessions.keys():
            await self._end(ctx, session)
        for key, session in sessions.items():
            await harness().report(ctx, self.id, session)
            self._sessions[key] = session

    async def _watch(self, ctx: CompanionContext) -> None:
        path = _socket_path()
        async with _exchange(path, "subscribe") as (result, reader):
            if result.get("subscribed") is not True:
                raise CapabilityError(_REQUEST_FAILED)
            loop = asyncio.get_running_loop()
            refresh_at = loop.time()
            while True:
                if loop.time() >= refresh_at:
                    await self._reconcile(ctx, path)
                    refresh_at = loop.time() + _RECONCILE_S
                try:
                    async with asyncio.timeout_at(refresh_at):
                        message = await _read(reader)
                except TimeoutError:
                    continue
                _check_error(message)
                if not (session := _session_id(message.get("session"))):
                    continue
                if message.get("event") == "state":
                    state = _state(message.get("state"))
                    if state == "ended":
                        await self._end(ctx, session)
                    elif current := self._sessions.get(session):
                        updated = replace(current, state=state)
                        await harness().report(ctx, self.id, updated)
                        self._sessions[session] = updated
                    else:
                        refresh_at = loop.time()
                elif message.get("event") == "final" and isinstance(text := message.get("text"), str) and text.strip():
                    ids = message.get("messageIds")
                    if not isinstance(ids, list):
                        continue
                    if delivery := self._prompting.get(session):
                        if not delivery.ended:
                            for value in ids:
                                if len(delivery.finals) >= 32:
                                    break
                                if message_id := _session_id(value):
                                    delivery.finals.setdefault(message_id, (ctx, text))
                    elif (message_id := self._receipts.get(session)) and message_id in ids:
                        del self._receipts[session]
                        await harness().reply(ctx, self.id, [f"{self.id}:{session}"], text)

    async def run(self, ctx: CompanionContext) -> None:
        await reconnect(lambda: self._watch(ctx))
