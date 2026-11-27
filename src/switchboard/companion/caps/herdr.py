from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..harness import STATES, Session, harness
from ._harness import HARNESS_ACTIONS, check_list_args, folder_name, prompt_args, reconnect
from .base import Capability, CapabilityError, CompanionContext, JobContext

HERDR_METHODS = frozenset({"ping", "agent.list", "agent.get", "agent.prompt", "events.subscribe", "session.snapshot"})
_STATUS_EVENT = "pane.agent_status_changed"
_LIFECYCLE_EVENTS = ("pane.agent_detected", "pane.exited")
_EVENTS = (_STATUS_EVENT, *_LIFECYCLE_EVENTS)
_IO_TIMEOUT = 5
_RECONCILE_S = 5
_MAX_LINE = 1024 * 1024
_REQUEST_ID = "swb"
_PANE_ID = re.compile(r"w[0-9A-HJKMNP-TV-Z]{1,16}:p[0-9A-HJKMNP-TV-Z]{1,16}")
_UNAVAILABLE = "Herdr is unavailable on this device."
_REQUEST_FAILED = "Herdr could not complete the request."
_ERRORS = {
    "agent_blocked": "Agent is waiting on a prompt.",
    "agent_not_running": "Agent is not running.",
    "agent_not_found": "Agent is not running.",
}


def _socket_path() -> Path:
    if path := os.environ.get("HERDR_SOCKET_PATH"):
        return Path(path)
    try:
        root = Path.home() / ".config" / "herdr"
    except RuntimeError:
        raise CapabilityError(_UNAVAILABLE) from None
    session = os.environ.get("HERDR_SESSION", "default")
    if session != "default":
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", session) or session in {".", ".."}:
            raise CapabilityError(_UNAVAILABLE)
        root = root / "sessions" / session
    return root / "herdr.sock"


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
    path: Path, method: str, params: dict[str, Any], response_type: str,
) -> AsyncIterator[tuple[dict[str, Any], asyncio.StreamReader]]:
    if method not in HERDR_METHODS:
        raise CapabilityError(_REQUEST_FAILED)
    writer = None
    try:
        try:
            async with asyncio.timeout(_IO_TIMEOUT):
                reader, writer = await asyncio.open_unix_connection(path, limit=_MAX_LINE)
                writer.write(json.dumps({"id": _REQUEST_ID, "method": method, "params": params}).encode("ascii") + b"\n")
                await writer.drain()
                message = await _read(reader)
        except CapabilityError:
            raise
        except (OSError, ValueError, NotImplementedError):
            raise CapabilityError(_UNAVAILABLE) from None
        if message.get("id") != _REQUEST_ID:
            raise CapabilityError(_REQUEST_FAILED)
        _check_error(message)
        result = message.get("result")
        if not isinstance(result, dict) or result.get("type") != response_type:
            raise CapabilityError(_REQUEST_FAILED)
        yield result, reader
    finally:
        if writer is not None:
            writer.close()
            with suppress(OSError):
                await asyncio.wait_for(writer.wait_closed(), _IO_TIMEOUT)


def _label(value: object) -> str:
    return value if isinstance(value, str) and value.isprintable() and "/" not in value and "\\" not in value else ""


def _pane_id(agent: dict[str, Any]) -> str:
    pane = agent.get("pane_id")
    return pane if isinstance(pane, str) and _PANE_ID.fullmatch(pane) else ""


async def _list_sessions(path: Path) -> dict[str, Session]:
    async with _exchange(path, "agent.list", {}, "agent_list") as (result, _):
        agents = result.get("agents")
        if not isinstance(agents, list):
            raise CapabilityError(_REQUEST_FAILED)
        sessions = {}
        for agent in agents:
            if not isinstance(agent, dict) or not (pane := _pane_id(agent)):
                raise CapabilityError(_REQUEST_FAILED)
            state = agent.get("agent_status")
            sessions[pane] = Session(
                harness="herdr", session=pane, agent=_label(agent.get("agent")), name=_label(agent.get("name")),
                dir=folder_name(agent.get("foreground_cwd")) or folder_name(agent.get("cwd")),
                state=state if state in STATES and state != "ended" else "unknown",
            )
        return sessions


async def _read_events(reader: asyncio.StreamReader, dirty: asyncio.Event) -> None:
    try:
        while True:
            message = await _read(reader)
            _check_error(message)
            if message.get("event") in _EVENTS:
                dirty.set()
    finally:
        dirty.set()


class HerdrCapability(Capability):
    id = "herdr"
    version = "1"
    title = "Herdr"
    actions = HARNESS_ACTIONS

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._prompting: set[str] = set()

    def available(self) -> str | None:
        if os.name == "posix":
            try:
                path = _socket_path()
                if os.environ.get("HERDR_SOCKET_PATH") or path.is_socket() or shutil.which("herdr"):
                    return None
            except (CapabilityError, OSError):
                pass
        return _UNAVAILABLE

    async def invoke(self, action: str, args: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
        if action == "list":
            check_list_args(args)
            return {"sessions": [session.payload() for session in (await _list_sessions(_socket_path())).values()]}
        if action != "prompt":
            raise CapabilityError("Choose a Herdr action.")
        target, text, conversation, agent = prompt_args(args)
        if not _PANE_ID.fullmatch(target):
            raise CapabilityError("Choose a Herdr pane ID.")
        if "\r" in text or "\t" in text:
            raise CapabilityError("Prompt text contains unsupported characters.")
        keys = [f"herdr:{target}"]
        if target in self._prompting:
            raise CapabilityError("A prompt is being sent to this Herdr pane.")
        self._prompting.add(target)
        try:
            async with _exchange(
                _socket_path(), "agent.prompt", {"target": target, "text": text}, "agent_prompted",
            ) as (result, _):
                reported = result.get("agent")
                if not isinstance(reported, dict) or reported.get("pane_id") != target:
                    raise CapabilityError(_REQUEST_FAILED)
                harness().expect(keys, target, conversation, agent)
        finally:
            self._prompting.remove(target)
        return {"delivered": True}

    async def run(self, ctx: CompanionContext) -> None:
        async def listen() -> None:
            path = _socket_path()
            panes: set[str] = set()
            while True:
                panes = await self._watch(ctx, path, panes)

        await reconnect(listen)

    async def _watch(self, ctx: CompanionContext, path: Path, panes: set[str]) -> set[str]:
        subscriptions = [
            *({"type": event} for event in _LIFECYCLE_EVENTS),
            *({"type": _STATUS_EVENT, "pane_id": pane} for pane in sorted(panes)),
        ]
        async with _exchange(path, "events.subscribe", {"subscriptions": subscriptions}, "subscription_started") as (_, reader):
            dirty = asyncio.Event()
            drain = asyncio.create_task(_read_events(reader, dirty))
            try:
                while True:
                    dirty.clear()
                    if drain.done():
                        await drain
                    sessions = await _list_sessions(path)
                    if drain.done():
                        await drain
                    for pane, session in sessions.items():
                        await harness().report(ctx, self.id, session)
                        self._sessions[pane] = session
                    for pane in self._sessions.keys() - sessions.keys():
                        keys = [f"herdr:{pane}"]
                        if (wake := harness().pending(keys)) and not harness().replying(wake):
                            harness().take(keys)
                        await harness().report(ctx, self.id, replace(self._sessions[pane], state="ended"))
                        del self._sessions[pane]
                    if panes != sessions.keys():
                        return set(sessions)
                    with suppress(TimeoutError):
                        async with asyncio.timeout(_RECONCILE_S):
                            await dirty.wait()
            finally:
                drain.cancel()
                await asyncio.gather(drain, return_exceptions=True)
