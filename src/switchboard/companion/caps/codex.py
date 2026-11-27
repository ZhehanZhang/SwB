from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout, ClientWebSocketResponse, UnixConnector, WSMsgType

from ... import __version__
from ..harness import REPLY_MAX, Session, Wake, harness
from ._harness import HARNESS_ACTIONS, check_list_args, folder_name, prompt_args, reconnect
from .base import Capability, CapabilityError, CompanionContext, JobContext

_UNAVAILABLE = "Codex app-server is unavailable."
_REJECTED = "Codex rejected the request."
_MISSING = "Choose a loaded Codex thread."
_POLL_S = 5.0
_RPC_TIMEOUT = 5.0
_OPAQUE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


class _Offline(CapabilityError):
    def __init__(self) -> None:
        super().__init__(_UNAVAILABLE)


class _Rejected(CapabilityError):
    def __init__(self) -> None:
        super().__init__(_REJECTED)


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _array(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _is_id(value: Any) -> bool:
    return isinstance(value, str) and _OPAQUE_ID.fullmatch(value) is not None


def _state(status: Any) -> str:
    status = _object(status)
    kind = status.get("type")
    if kind == "active":
        flags = _array(status.get("activeFlags"))
        return "blocked" if "waitingOnApproval" in flags or "waitingOnUserInput" in flags else "working"
    if kind == "idle":
        return "idle"
    if kind == "notLoaded":
        return "ended"
    return "unknown"


def _answer(item: Any) -> tuple[str, bool] | None:
    item = _object(item)
    text, phase = item.get("text"), item.get("phase")
    if (item.get("type") == "agentMessage" and phase in (None, "final_answer")
            and isinstance(text, str) and text.strip()):
        return text[:REPLY_MAX], phase == "final_answer"
    return None


@dataclass
class _Waiting:
    turn: str
    wake: Wake
    legacy: bool
    text: str = ""
    final: bool = False
    empty_since: float | None = None
    legacy_cursor: str | None = None

    def capture(self, item: Any) -> None:
        if (answer := _answer(item)) and (answer[1] or not self.final):
            self.text, self.final = answer


class _Connection:
    def __init__(self, ws: ClientWebSocketResponse, watched: set[str]) -> None:
        self.ws, self.watched = ws, watched
        self.events: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue(maxsize=256)
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.next_id = 0
        self.closed = False

    async def read(self) -> None:
        try:
            async for frame in self.ws:
                if frame.type != WSMsgType.TEXT:
                    break
                message = _object(json.loads(frame.data))
                method, request_id = message.get("method"), message.get("id")
                if "id" in message:
                    if method is None and type(request_id) is int:
                        future = self.pending.get(request_id)
                        if future is not None and not future.done():
                            if "error" not in message and isinstance(message.get("result"), dict):
                                future.set_result(message["result"])
                            else:
                                future.set_exception(_Rejected())
                    continue
                if not isinstance(method, str):
                    continue
                params = _object(message.get("params"))
                thread = params.get("threadId")
                if not _is_id(thread):
                    continue
                if method in ("thread/status/changed", "thread/closed"):
                    self.events.put_nowait((method, params))
                elif thread in self.watched and (method == "turn/completed"
                                                 or (method == "item/completed" and _answer(params.get("item")))):
                    self.events.put_nowait((method, params))
        except (ClientError, OSError, ValueError, RecursionError, asyncio.QueueFull):
            pass
        finally:
            self.closed = True
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(_Offline())
            if self.events.full():
                self.events.get_nowait()
            self.events.put_nowait(None)

    async def send(self, message: dict[str, Any]) -> None:
        if self.closed:
            raise _Offline()
        try:
            await self.ws.send_json(message)
        except (ClientError, OSError, RuntimeError):
            raise _Offline() from None

    async def call(self, method: str, **params: Any) -> dict[str, Any]:
        self.next_id += 1
        request_id = self.next_id
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            async with asyncio.timeout(_RPC_TIMEOUT):
                await self.send({"id": request_id, "method": method, "params": params})
                return await future
        except TimeoutError:
            raise _Offline() from None
        finally:
            self.pending.pop(request_id, None)
            if future.done() and not future.cancelled():
                future.exception()

    async def pages(self, method: str, *, max_pages: int, **params: Any) -> AsyncIterator[dict[str, Any]]:
        seen = set()
        for _ in range(max_pages):
            result = await self.call(method, **params)
            data, cursor = result.get("data"), result.get("nextCursor")
            if not isinstance(data, list) or (cursor is not None and not isinstance(cursor, str)):
                raise _Rejected()
            yield result
            if not cursor:
                return
            if cursor in seen:
                raise _Rejected()
            seen.add(cursor)
            params["cursor"] = cursor
        raise _Rejected()


class CodexCapability(Capability):
    id = "codex"
    version = "1.0.0"
    title = "Codex"
    actions = HARNESS_ACTIONS

    def __init__(self) -> None:
        override = os.environ.get("SWB_CODEX_SOCKET")
        home = os.environ.get("CODEX_HOME")
        self._configured = bool(override)
        try:
            if override:
                self.path = Path(override).expanduser() if "\0" not in override else None
            else:
                root = Path(home) if home else Path.home() / ".codex"
                self.path = root / "app-server-control" / "app-server-control.sock"
        except (OSError, RuntimeError):
            self.path = None
        self._rpc: _Connection | None = None
        self._lock = asyncio.Lock()
        self._sessions: dict[str, Session] = {}
        self._turns: dict[str, _Waiting] = {}

    def available(self) -> str | None:
        if os.name != "posix" or self.path is None:
            return _UNAVAILABLE
        try:
            if self._configured or self.path.is_socket() or shutil.which("codex"):
                return None
        except OSError:
            pass
        return _UNAVAILABLE

    async def _loaded(self, rpc: _Connection) -> set[str]:
        loaded: set[str] = set()
        async for page in rpc.pages("thread/loaded/list", max_pages=32):
            data = page["data"]
            if any(not _is_id(thread) for thread in data):
                raise _Rejected()
            loaded.update(data)
        return loaded

    async def _thread(self, rpc: _Connection, thread: str) -> dict[str, Any]:
        value = _object((await rpc.call("thread/read", threadId=thread, includeTurns=False)).get("thread"))
        if value.get("id") != thread:
            raise _Rejected()
        return value

    async def _snapshot(self, rpc: _Connection) -> dict[str, Session]:
        sessions = {}
        for thread in sorted(await self._loaded(rpc)):
            try:
                info = await self._thread(rpc, thread)
            except _Rejected:
                sessions[thread] = self._sessions.get(thread, Session(self.id, thread, self.id))
                continue
            state = _state(info.get("status"))
            if state != "ended":
                sessions[thread] = Session(self.id, thread, self.id, dir=folder_name(info.get("cwd")), state=state)
        return sessions

    async def invoke(self, action: str, args: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
        if action == "list":
            check_list_args(args)
            if (rpc := self._rpc) is None:
                raise _Offline()
            return {"sessions": [session.payload() for session in (await self._snapshot(rpc)).values()]}
        if action != "prompt":
            raise CapabilityError("Choose a Codex action.")
        thread, text, conversation, agent = prompt_args(args)
        async with self._lock:
            if (rpc := self._rpc) is None:
                raise _Offline()
            if thread not in await self._loaded(rpc):
                raise CapabilityError(_MISSING)
            info = await self._thread(rpc, thread)
            state = _state(info.get("status"))
            if state == "blocked":
                raise CapabilityError("The Codex thread is blocked.")
            if state not in ("working", "idle"):
                raise CapabilityError(_MISSING)
            key = f"codex:{thread}"
            if self._waiting(rpc, thread) or harness().pending([key]):
                raise CapabilityError("The Codex thread has a pending wake.")
            params = {"threadId": thread, "input": [{"type": "text", "text": text, "text_elements": []}]}
            rpc.watched.add(thread)
            try:
                if state == "working":
                    result = await rpc.call("thread/turns/list", threadId=thread, limit=1, itemsView="notLoaded")
                    turns = _array(result.get("data"))
                    turn = _object(turns[0]) if turns else {}
                    expected = turn.get("id")
                    if turn.get("status") != "inProgress" or not _is_id(expected):
                        raise CapabilityError("The active Codex turn is unavailable.")
                    accepted = (await rpc.call("turn/steer", expectedTurnId=expected, **params)).get("turnId")
                    if accepted != expected:
                        raise _Rejected()
                else:
                    accepted = _object((await rpc.call("turn/start", **params)).get("turn")).get("id")
                    if not _is_id(accepted):
                        raise _Rejected()
                harness().expect([key], thread, conversation, agent)
                if wake := harness().pending([key]):
                    self._turns[thread] = _Waiting(accepted, wake, info.get("historyMode") == "legacy")
            finally:
                if thread not in self._turns:
                    rpc.watched.discard(thread)
        return {"delivered": True}

    def _waiting(self, rpc: _Connection, thread: str) -> _Waiting | None:
        wait = self._turns.get(thread)
        if wait and harness().pending([f"codex:{thread}"]) is wait.wake:
            return wait
        self._turns.pop(thread, None)
        rpc.watched.discard(thread)
        return None

    async def _refresh(self, rpc: _Connection, ctx: CompanionContext) -> None:
        sessions = await self._snapshot(rpc)
        for thread in self._sessions.keys() - sessions.keys():
            await harness().report(ctx, self.id, replace(self._sessions[thread], state="ended"))
        for session in sessions.values():
            await harness().report(ctx, self.id, session)
        self._sessions = sessions
        for thread in tuple(self._turns):
            if wait := self._waiting(rpc, thread):
                await self._reconcile_turn(rpc, ctx, thread, wait)

    async def _reconcile_turn(self, rpc: _Connection, ctx: CompanionContext, thread: str, wait: _Waiting) -> None:
        try:
            async for page in rpc.pages("thread/turns/list", max_pages=8, threadId=thread,
                                        limit=1 if wait.legacy else 16, itemsView="notLoaded"):
                for value in page["data"]:
                    turn = _object(value)
                    if turn.get("id") == wait.turn:
                        break
                else:
                    continue
                cursor = page.get("backwardsCursor")
                if wait.legacy and isinstance(cursor, str) and cursor:
                    wait.legacy_cursor = cursor
                await self._completed(rpc, ctx, thread, wait, turn)
                return
        except _Rejected:
            return

    async def _history_answer(self, rpc: _Connection, thread: str, wait: _Waiting) -> str:
        if wait.legacy:
            if not wait.legacy_cursor:
                raise _Rejected()
            result = await rpc.call("thread/turns/list", threadId=thread, limit=1, itemsView="full",
                                    cursor=wait.legacy_cursor, sortDirection="asc")
            for turn in map(_object, _array(result.get("data"))):
                if turn.get("id") == wait.turn:
                    for item in _array(turn.get("items")):
                        wait.capture(item)
                    return wait.text
            raise _Rejected()
        fallback = ""
        async for page in rpc.pages("thread/items/list", max_pages=8, threadId=thread, turnId=wait.turn,
                                    limit=16, sortDirection="desc"):
            for entry in map(_object, page["data"]):
                if entry.get("turnId") == wait.turn and (answer := _answer(entry.get("item"))):
                    if answer[1]:
                        return answer[0]
                    fallback = fallback or answer[0]
        return fallback

    async def _completed(self, rpc: _Connection, ctx: CompanionContext, thread: str, wait: _Waiting,
                         turn: dict[str, Any]) -> None:
        status = turn.get("status")
        if status not in ("completed", "failed", "interrupted"):
            return
        text = ""
        if status == "completed":
            for item in _array(turn.get("items")):
                wait.capture(item)
            try:
                text = wait.text or await self._history_answer(rpc, thread, wait)
            except _Rejected:
                return
            if not text:
                now = time.monotonic()
                if wait.empty_since is None:
                    wait.empty_since = now
                if now - wait.empty_since < _POLL_S:
                    return
        if self._waiting(rpc, thread) is not wait:
            return
        self._turns.pop(thread)
        rpc.watched.discard(thread)
        key = f"codex:{thread}"
        if text:
            await harness().reply(ctx, self.id, [key], text)
        else:
            harness().take([key])

    async def _event(self, rpc: _Connection, ctx: CompanionContext, method: str, params: dict[str, Any]) -> None:
        thread = params["threadId"]
        if method in ("thread/status/changed", "thread/closed"):
            if session := self._sessions.get(thread):
                state = "ended" if method == "thread/closed" else _state(params.get("status"))
                if state != session.state:
                    updated = replace(session, state=state)
                    await harness().report(ctx, self.id, updated)
                    if state == "ended":
                        self._sessions.pop(thread)
                    else:
                        self._sessions[thread] = updated
        elif wait := self._waiting(rpc, thread):
            if method == "item/completed" and params.get("turnId") == wait.turn:
                wait.capture(params.get("item"))
            elif method == "turn/completed" and (turn := _object(params.get("turn"))).get("id") == wait.turn:
                await self._completed(rpc, ctx, thread, wait, turn)

    async def _listen(self, ctx: CompanionContext) -> None:
        if self.path is None:
            raise _Offline()
        timeout = ClientTimeout(total=_RPC_TIMEOUT)
        async with ClientSession(connector=UnixConnector(self.path), timeout=timeout, trust_env=False) as client:
            async with client.ws_connect("ws://localhost/rpc", heartbeat=15) as ws:
                rpc = _Connection(ws, set(self._turns))
                reader = asyncio.create_task(rpc.read())
                try:
                    await rpc.call("initialize", clientInfo={"name": "swb", "title": "SwB", "version": __version__})
                    await rpc.send({"method": "initialized"})
                    self._rpc = rpc
                    loop = asyncio.get_running_loop()
                    next_poll = 0.0
                    while True:
                        if loop.time() >= next_poll:
                            async with self._lock:
                                await self._refresh(rpc, ctx)
                            next_poll = loop.time() + _POLL_S
                        try:
                            async with asyncio.timeout_at(next_poll):
                                event = await rpc.events.get()
                        except TimeoutError:
                            continue
                        if event is None:
                            return
                        async with self._lock:
                            await self._event(rpc, ctx, *event)
                finally:
                    self._rpc = None
                    reader.cancel()
                    with suppress(asyncio.CancelledError):
                        await reader

    async def run(self, ctx: CompanionContext) -> None:
        await reconnect(lambda: self._listen(ctx))
