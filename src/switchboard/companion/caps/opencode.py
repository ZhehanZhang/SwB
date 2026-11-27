from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..client import Disconnected
from ..harness import REFRESH_S, Session, harness
from ._harness import HARNESS_ACTIONS, check_list_args, folder_name, prompt_args, reconnect
from .base import Capability, CapabilityError, CompanionContext, JobContext

_SET_URL = "Set SWB_OPENCODE_URL to use OpenCode."
_BAD_URL = "Use a loopback HTTP or HTTPS URL for OpenCode."
_UNAVAILABLE = "OpenCode API routes are unavailable."
_FAILED = "OpenCode did not respond as expected."
_BLOCKED = "Resolve the pending approval in OpenCode."
_SESSION_UNAVAILABLE = "The OpenCode session is unavailable."
_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_STATES = {"busy": "working", "running": "working", "retry": "working", "idle": "idle"}
_MAX_EVENT = 1 << 20
_ACTIVE_REFRESH_S = 5
_HISTORY_PAGES = 6


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> list[Any]:
    values = _obj(value).get("data") if isinstance(value, dict) else value
    if not isinstance(values, list):
        raise CapabilityError(_FAILED)
    return values


def _session_id(value: Any) -> str:
    return value if isinstance(value, str) and _SESSION_ID.fullmatch(value) else ""


def _route(route: str, session: str) -> str:
    return route.replace("{sessionID}", session).replace("{id}", session)


@dataclass(frozen=True)
class _Routes:
    sessions: str
    events: str
    prompt: str
    messages: str
    body: str
    session: str
    status: str
    permissions: str
    forms: str
    all_permissions: str
    all_forms: str
    directory_params: frozenset[str]
    wait: str


def _discover(document: Any) -> _Routes:
    document = _obj(document)
    paths = _obj(document.get("paths"))

    def operation(path: str, method: str) -> Any:
        return _obj(paths.get(path)).get(method)

    def select(*candidates: str, method: str = "get") -> str:
        for path in candidates:
            for template in dict.fromkeys((path, path.replace("{sessionID}", "{id}"))):
                if isinstance(operation(template, method), dict):
                    return template
        return ""

    for prefix in ("/api", ""):
        sessions = select(prefix + "/session")
        events = select("/api/event") if prefix else select("/event", "/global/event")
        single = prefix + "/session/{sessionID}"
        prompt = select(single + ("/prompt" if prefix else "/prompt_async"), method="post")
        messages = select(single + "/message")
        if not all((sessions, events, prompt, messages)):
            continue
        body = "legacy"
        if prefix:
            request = _obj(operation(prompt, "post").get("requestBody"))
            schema = _obj(_obj(_obj(request.get("content")).get("application/json")).get("schema"))
            if (ref := schema.get("$ref")) and isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                schema = _obj(_obj(_obj(document.get("components")).get("schemas")).get(ref.split("/")[-1]))
            properties = _obj(schema.get("properties"))
            if "text" in properties:
                body = "text"
            elif "prompt" in properties:
                body = "prompt"
            else:
                continue
        permissions = select(single + "/permission") if prefix else select("/permission")
        forms = select(single + "/form", single + "/question") if prefix else select("/question")
        directory_params = frozenset(
            path for path, method in ((prompt, "post"), (messages, "get"), (permissions, "get"), (forms, "get"))
            if not prefix and path and any(
                _obj(param).get("name") == "directory" and _obj(param).get("in") == "query"
                for param in _obj(operation(path, method)).get("parameters", []) or []
            )
        )
        return _Routes(
            sessions, events, prompt, messages, body, select(single),
            select("/api/session/active", "/api/session/status") if prefix else select("/session/status"),
            permissions, forms,
            select("/api/permission/request") if prefix else select("/permission"),
            select("/api/form", "/api/question/request") if prefix else select("/question"),
            directory_params,
            select(single + "/wait", method="post") if body == "prompt" else "",
        )
    raise CapabilityError(_UNAVAILABLE)


async def _get(client: httpx.AsyncClient, path: str, **kwargs: Any) -> Any:
    response = await client.get(path, **kwargs)
    response.raise_for_status()
    return response.json()


async def _events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    data: list[str] = []
    size = 0
    async for line in response.aiter_lines():
        if not line:
            if data and size <= _MAX_EVENT:
                try:
                    value = json.loads("\n".join(data))
                    if isinstance(value, str):
                        value = json.loads(value)
                except (ValueError, RecursionError):
                    value = None
                if isinstance(value, dict):
                    yield _obj(value.get("payload", value))
            data.clear()
            size = 0
        elif line.startswith("data:"):
            size += len(line)
            if size <= _MAX_EVENT:
                data.append(line[5:].removeprefix(" "))


def _archive(info: dict[str, Any]) -> bool:
    return _obj(info.get("time")).get("archived") is not None


def _folder(info: dict[str, Any]) -> str:
    return folder_name(info.get("directory") or _obj(info.get("location")).get("directory"))


async def _history_reply(
    client: httpx.AsyncClient, routes: _Routes, session: str, directory: str, message_id: str,
) -> tuple[bool, str]:
    legacy = routes.body == "legacy"
    params: dict[str, Any] = {"limit": 50}
    if not legacy:
        params["order"] = "desc"
    if directory and routes.messages in routes.directory_params:
        params["directory"] = directory
    path = _route(routes.messages, session)
    cursors: set[str] = set()
    latest = ""
    finished = False
    for _ in range(_HISTORY_PAGES):
        response = await client.get(path, params=params)
        response.raise_for_status()
        page = response.json()
        items = _items(page)
        for item in map(_obj, reversed(items) if legacy else items):
            info = _obj(item.get("info")) if legacy else item
            role = info.get("role" if legacy else "type")
            if role == "user":
                if info.get("id") == message_id:
                    return finished, latest
                if not legacy:
                    finished, latest = False, ""
                continue
            if role == "idle" and not legacy:
                finished = True
            parent = info.get("parentID")
            if role != "assistant" or latest or info.get("summary") or (legacy and parent != message_id):
                continue
            if not legacy and parent is not None and parent != message_id:
                continue
            if info.get("error"):
                finished = True
                continue
            parts = item.get("parts" if legacy else "content")
            if isinstance(parts, list):
                latest = "\n".join(
                    part["text"] for part in map(_obj, parts)
                    if part.get("type") == "text" and isinstance(part.get("text"), str)
                    and part["text"].strip() and not part.get("synthetic") and not part.get("ignored")
                )
            finished |= bool(latest) or _obj(info.get("time")).get("completed") is not None
            if latest and parent == message_id:
                return True, latest
        cursor = response.headers.get("X-Next-Cursor") if legacy else _obj(_obj(page).get("cursor")).get("next")
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            break
        cursors.add(cursor)
        params.pop("order", None)
        params["before" if legacy else "cursor"] = cursor
    return (finished, "") if legacy else (False, "")


class OpenCodeCapability(Capability):
    id = "opencode"
    version = "1"
    title = "OpenCode"
    actions = HARNESS_ACTIONS

    def __init__(self) -> None:
        self._url = os.environ.get("SWB_OPENCODE_URL", "").strip()
        password = os.environ.get("OPENCODE_SERVER_PASSWORD")
        self._auth = ("opencode", password) if password else None
        self._routes: _Routes | None = None
        self._default_state = "unknown"
        self._states: dict[str, str] = {}
        self._state_revision = 0
        self._dirs: dict[str, str] = {}
        self._pending: dict[str, set[tuple[str, str]]] = {}
        self._expected: dict[str, tuple[str, str]] = {}
        self._lock = asyncio.Lock()
        self._last_id = 0

    def available(self) -> str | None:
        if not self._url:
            return _SET_URL
        try:
            parts = urlsplit(self._url)
            if (parts.scheme in {"http", "https"} and parts.hostname and "@" not in parts.netloc
                    and "?" not in self._url and "#" not in self._url
                    and all(char.isprintable() and not char.isspace() for char in self._url)
                    and (parts.hostname == "localhost" or ip_address(parts.hostname).is_loopback)):
                httpx.URL(self._url)
                return None
        except (ValueError, httpx.InvalidURL):
            pass
        return _BAD_URL

    def _client(self) -> httpx.AsyncClient:
        if reason := self.available():
            raise CapabilityError(reason)
        return httpx.AsyncClient(base_url=self._url, auth=self._auth, timeout=10, trust_env=False, follow_redirects=False)

    async def _find_routes(self, client: httpx.AsyncClient) -> _Routes:
        try:
            return _discover(await _get(client, "/doc"))
        except (httpx.HTTPError, ValueError, RecursionError):
            raise CapabilityError(_UNAVAILABLE) from None

    def _state(self, session: str) -> str:
        return "blocked" if self._pending.get(session) else self._states.get(session, self._default_state)

    def _session(self, session: str) -> Session:
        return Session(harness=self.id, session=session, agent=self.id, dir=self._dirs.get(session, ""),
                       state=self._state(session))

    async def _end(self, ctx: CompanionContext, session: str) -> None:
        self._state_revision += 1
        self._states[session] = "ended"
        self._pending.pop(session, None)
        await harness().report(ctx, self.id, self._session(session))
        self._states.pop(session, None)
        self._dirs.pop(session, None)
        keys = [f"{self.id}:{session}"]
        if not (wake := harness().pending(keys)) or not harness().replying(wake):
            self._expected.pop(session, None)
            harness().take(keys)

    async def _list(self, client: httpx.AsyncClient, routes: _Routes) -> list[Session]:
        directories: dict[str, str] = {}
        cursors: set[str] = set()
        params: dict[str, str] = {}
        while True:
            page = await _get(client, routes.sessions, params=params)
            for info in map(_obj, _items(page)):
                if (session := _session_id(info.get("id"))) and not _archive(info):
                    directories[session] = _folder(info)
            cursor = _obj(_obj(page).get("cursor")).get("next")
            if routes.body == "legacy" or cursor is None:
                self._dirs.update(directories)
                return [self._session(session) for session in directories]
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise CapabilityError(_FAILED)
            cursors.add(cursor)
            params["cursor"] = cursor

    async def _prompt(
        self, client: httpx.AsyncClient, routes: _Routes, session: str, text: str, conversation: str, agent: str,
    ) -> dict[str, Any]:
        if not routes.permissions or not routes.forms:
            raise CapabilityError(_UNAVAILABLE)
        key = f"{self.id}:{session}"
        async with self._lock:
            if self._pending.get(session):
                raise CapabilityError(_BLOCKED)
            monitor = harness()
            for old_session in tuple(self._expected):
                if not monitor.pending([f"{self.id}:{old_session}"]):
                    self._expected.pop(old_session)
            if session in self._expected:
                raise CapabilityError("Wait for the pending OpenCode reply.")
            directory = ""
            if routes.session:
                response = await client.get(_route(routes.session, session))
                if response.status_code == 404:
                    raise CapabilityError(_SESSION_UNAVAILABLE)
                response.raise_for_status()
                info = _obj(response.json())
                current = info if routes.body == "legacy" else _obj(info.get("data"))
                if current.get("id") != session or _archive(current):
                    raise CapabilityError(_SESSION_UNAVAILABLE)
                if routes.body == "legacy" and isinstance(current.get("directory"), str):
                    directory = current["directory"]
            elif session not in {item.session for item in await self._list(client, routes)}:
                raise CapabilityError(_SESSION_UNAVAILABLE)
            for path in (routes.permissions, routes.forms):
                params = {"directory": directory} if directory and path in routes.directory_params else {}
                pending = _items(await _get(client, _route(path, session), params=params))
                if self._pending.get(session) or any(
                    routes.body != "legacy" or _obj(item).get("sessionID", session) == session for item in pending
                ):
                    raise CapabilityError(_BLOCKED)
            self._last_id = max(self._last_id + 1, time.time_ns() // 1_000_000 << 12)
            message_id = f"msg_{self._last_id:012x}{secrets.token_hex(7)}"
            if routes.body == "legacy":
                body = {"messageID": message_id, "parts": [{"type": "text", "text": text}]}
            else:
                content = {"prompt": {"text": text}} if routes.body == "prompt" else {"text": text}
                body = {"id": message_id, "delivery": "queue", **content}
            params = {"directory": directory} if directory and routes.prompt in routes.directory_params else {}
            response = await client.post(_route(routes.prompt, session), json=body, params=params)
            response.raise_for_status()
            if routes.body == "legacy":
                if response.status_code != 204:
                    raise CapabilityError(_FAILED)
            elif _obj(_obj(response.json()).get("data")).get("id") != message_id:
                raise CapabilityError(_FAILED)
            monitor.expect([key], session, conversation, agent)
            self._expected[session] = (message_id, directory)
            return {"queued": True}

    async def invoke(self, action: str, args: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
        if action == "list":
            check_list_args(args)
        elif action == "prompt":
            session, text, conversation, agent = prompt_args(args)
            if not _session_id(session):
                raise CapabilityError(_SESSION_UNAVAILABLE)
        else:
            raise CapabilityError("This OpenCode action is unavailable.")
        try:
            async with self._client() as client:
                routes = self._routes or await self._find_routes(client)
                if action == "list":
                    return {"sessions": [item.payload() for item in await self._list(client, routes)]}
                return await self._prompt(client, routes, session, text, conversation, agent)
        except CapabilityError:
            raise
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, RecursionError):
            raise CapabilityError(_FAILED) from None

    async def _reply(self, client: httpx.AsyncClient, routes: _Routes, ctx: CompanionContext, session: str) -> None:
        key = f"{self.id}:{session}"
        async with self._lock:
            monitor = harness()
            if (not (expected := self._expected.get(session)) or self._pending.get(session)
                    or not (wake := monitor.pending([key])) or monitor.replying(wake)):
                return
            message_id, directory = expected
            finished, text = await _history_reply(client, routes, session, directory, message_id)
            if not finished or monitor.pending([key]) is not wake:
                return
            if text:
                if not await monitor.reply(ctx, self.id, [key], text):
                    return
            else:
                monitor.take([key])
            self._expected.pop(session, None)

    async def _event(
        self, client: httpx.AsyncClient, routes: _Routes, ctx: CompanionContext, event: dict[str, Any],
    ) -> None:
        kind = event.get("type")
        if not isinstance(kind, str):
            return
        data = _obj(event.get("data", event.get("properties")))
        info = _obj(data.get("info"))
        item = _obj(data.get("form", data))
        session = _session_id(item.get("sessionID") or data.get("sessionID") or info.get("id"))
        if not session:
            return
        if directory := _folder(info or data) or _folder(event):
            self._dirs[session] = directory
        state = ""
        if kind in {"session.status", "session.idle", "session.execution.started", "session.execution.succeeded",
                    "session.execution.failed", "session.execution.interrupted", "session.next.step.started"}:
            status = _obj(data.get("status")).get("type")
            if kind == "session.status":
                if isinstance(status, str):
                    state = _STATES.get(status, "")
            else:
                state = "working" if kind in {"session.execution.started", "session.next.step.started"} else "idle"
        elif kind in {"session.deleted", "session.archived"} or (kind == "session.updated" and _archive(info)):
            await self._end(ctx, session)
            return
        elif kind in {"session.created", "session.updated", "session.moved", "session.unarchived"}:
            await harness().report(ctx, self.id, self._session(session))
            return
        else:
            family, _, action = kind.replace(".v2.", ".").partition(".")
            if family not in {"permission", "question", "form"}:
                return
            request = item.get("requestID", item.get("id"))
            if not isinstance(request, str) or not request:
                return
            if action in {"asked", "created"}:
                self._pending.setdefault(session, set()).add((family, request))
            elif action in {"replied", "rejected", "cancelled"}:
                if pending := self._pending.get(session):
                    pending.discard((family, request))
                    if not pending:
                        self._pending.pop(session, None)
            else:
                return
        if state:
            self._state_revision += 1
            self._states[session] = state
        await harness().report(ctx, self.id, self._session(session))
        if state == "idle" and kind not in {"session.execution.failed", "session.execution.interrupted"}:
            await self._reply(client, routes, ctx, session)

    async def _refresh_states(self, client: httpx.AsyncClient, routes: _Routes) -> bool:
        revision = self._state_revision
        states = {}
        if routes.status:
            statuses = await _get(client, routes.status)
            if isinstance(statuses, dict):
                statuses = statuses.get("data", statuses)
            if not isinstance(statuses, dict):
                raise CapabilityError(_FAILED)
            for session, value in statuses.items():
                if _session_id(session) and isinstance(status := _obj(value).get("type"), str):
                    states[session] = _STATES.get(status, "unknown")
        if revision != self._state_revision:
            return False
        self._states = states
        self._default_state = "idle" if routes.status == "/api/session/active" else "unknown"
        return True

    async def _poll_active(self, client: httpx.AsyncClient, routes: _Routes, ctx: CompanionContext) -> None:
        while True:
            await asyncio.sleep(_ACTIVE_REFRESH_S)
            if not await self._refresh_states(client, routes):
                continue
            for session in tuple(self._dirs):
                if session in self._dirs:
                    await harness().report(ctx, self.id, self._session(session))

    async def _poll_sessions(self, client: httpx.AsyncClient, routes: _Routes, ctx: CompanionContext) -> None:
        while True:
            await asyncio.sleep(REFRESH_S)
            await self._snapshot(client, routes, ctx)

    async def _snapshot(self, client: httpx.AsyncClient, routes: _Routes, ctx: CompanionContext) -> None:
        await self._refresh_states(client, routes)
        self._pending.clear()
        for family, path in (("permission", routes.all_permissions), (
            "form" if routes.all_forms == "/api/form" else "question", routes.all_forms,
        )):
            if path:
                for item in map(_obj, _items(await _get(client, path))):
                    if (session := _session_id(item.get("sessionID"))) and isinstance(request := item.get("id"), str):
                        self._pending.setdefault(session, set()).add((family, request))
        previous = set(self._dirs)
        sessions = await self._list(client, routes)
        for session in sorted(previous - {item.session for item in sessions}):
            await self._end(ctx, session)
        for session in sessions:
            await harness().report(ctx, self.id, session)

    async def _wait_idle(
        self, client: httpx.AsyncClient, routes: _Routes, ctx: CompanionContext, session: str, expected: tuple[str, str],
    ) -> None:
        if not (wake := harness().pending([f"{self.id}:{session}"])):
            return
        remaining = wake.expires - time.monotonic()
        if remaining <= 0:
            return
        try:
            async with asyncio.timeout(remaining):
                result = await client.post(_route(routes.wait, session), timeout=httpx.Timeout(10, read=remaining))
                result.raise_for_status()
                if result.status_code == 204 and self._expected.get(session) == expected:
                    await self._event(client, routes, ctx, {"type": "session.idle", "data": {"sessionID": session}})
        except (TimeoutError, httpx.HTTPError):
            pass

    async def run(self, ctx: CompanionContext) -> None:
        if self.available():
            return

        async def listen() -> None:
            try:
                async with self._client() as client:
                    routes = await self._find_routes(client)
                    async with client.stream(
                        "GET", routes.events, headers={"Accept": "text/event-stream"}, timeout=httpx.Timeout(10, read=75),
                    ) as response:
                        response.raise_for_status()
                        if response.headers.get("content-type", "").partition(";")[0] != "text/event-stream":
                            raise CapabilityError(_UNAVAILABLE)
                        self._routes = routes
                        await self._snapshot(client, routes, ctx)
                        try:
                            async with asyncio.TaskGroup() as tasks:
                                waiters: dict[str, asyncio.Task[None]] = {}
                                poll = tasks.create_task(
                                    self._poll_active(client, routes, ctx) if routes.status == "/api/session/active"
                                    else self._poll_sessions(client, routes, ctx),
                                )

                                def schedule(session: str, message_id: Any = None) -> None:
                                    if not routes.wait or not (expected := self._expected.get(session)):
                                        return
                                    if message_id is not None and expected[0] != message_id:
                                        return
                                    if session not in waiters or waiters[session].done():
                                        waiters[session] = tasks.create_task(
                                            self._wait_idle(client, routes, ctx, session, expected),
                                        )

                                try:
                                    for session in self._expected:
                                        schedule(session)
                                    async for event in _events(response):
                                        await self._event(client, routes, ctx, event)
                                        if routes.wait and event.get("type") == "session.next.prompted":
                                            data = _obj(event.get("data", event.get("properties")))
                                            if (session := _session_id(data.get("sessionID"))) and isinstance(
                                                message_id := data.get("messageID"), str,
                                            ):
                                                async with self._lock:
                                                    schedule(session, message_id)
                                finally:
                                    for task in waiters.values():
                                        task.cancel()
                                    poll.cancel()
                        except* Disconnected:
                            raise Disconnected from None
            finally:
                self._routes = None

        await reconnect(listen)
