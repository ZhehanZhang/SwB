from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from ..mcp_config import LocalServer
from .base import ActionSpec, Capability, CapabilityError, CompanionContext, JobContext

CALL_TIMEOUT_S = 110
PROBE_TIMEOUT_S = 3
RESULT_LIMIT = 1024 * 1024
FAILED = "Local MCP request failed."
TIMED_OUT = "Local MCP request timed out."
TOO_LARGE = "Local MCP result exceeds 1 MiB."
REFUSED = "Local MCP connection refused."
TOOL_ERROR = "Local MCP tool returned an error."
_PLAIN_ERROR = re.compile(r"[A-Za-z]{1,24}(?:[,.?!]? +[A-Za-z]{1,24})*[.?!]?")
_CREDENTIAL_WORD = re.compile(r"\b(?:authorization|bearer|credential|key|password|secret|token)s?\b", re.I)
_SERVER = {"type": "string", "description": "A server name from mcp.servers."}
_QUIET_LOGGERS = ("mcp.client.stdio", "mcp.client.session", "mcp.client.streamable_http", "mcp.shared.jsonrpc_dispatcher",
                  "mcp.shared.tool_name_validation", "httpx2", "httpcore2")


def _json_size(data: Any) -> int:
    return len(json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))


def _tool_error(text: str) -> CapabilityError:
    text = text.strip()
    if len(text) <= 160 and _PLAIN_ERROR.fullmatch(text) and not _CREDENTIAL_WORD.search(text):
        return CapabilityError(f"{TOOL_ERROR} {text}")
    return CapabilityError(TOOL_ERROR)


class _LoopbackTransport(httpx2.AsyncHTTPTransport):
    def __init__(self, url: httpx2.URL, fail: Callable[[str], None]):
        super().__init__(trust_env=False)
        self.origin = url.scheme, url.host, url.port
        self.fail = fail

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        url = request.url
        if (url.scheme, url.host, url.port) != self.origin:
            raise httpx2.ConnectError(FAILED)
        if request.method == "DELETE":
            request.extensions.setdefault("timeout", {})["read"] = 2.0
        try:
            response = await self._request_loopback(request)
        except (httpx2.ConnectError, httpx2.TimeoutException) as error:
            if request.method == "POST":
                self.fail(TIMED_OUT if isinstance(error, httpx2.TimeoutException) else REFUSED)
            raise
        if request.method == "POST" and not response.is_success:
            reason = f"Local MCP returned HTTP {response.status_code}."
            self.fail(reason)
            await response.aclose()
            raise CapabilityError(reason)
        return response

    async def _request_loopback(self, request: httpx2.Request) -> httpx2.Response:
        url = request.url
        if url.host != "localhost":
            return await super().handle_async_request(request)
        try:
            request.url = url.copy_with(host="127.0.0.1")
            try:
                return await super().handle_async_request(request)
            except (httpx2.ConnectError, httpx2.ConnectTimeout):
                request.url = url.copy_with(host="::1")
                return await super().handle_async_request(request)
        finally:
            request.url = url


class _WatchStdout:
    def __init__(self, stream: Any, stop: asyncio.Event):
        self.stream, self.stop = stream, stop

    async def __aenter__(self) -> _WatchStdout:
        await self.stream.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.stream.__aexit__(*exc)

    def __aiter__(self) -> _WatchStdout:
        return self

    async def __anext__(self) -> Any:
        try:
            return await anext(self.stream)
        except StopAsyncIteration:
            self.stop.set()
            raise

    async def receive(self) -> Any:
        return await self.stream.receive()

    async def aclose(self) -> None:
        await self.stream.aclose()


@asynccontextmanager
async def _transport(server: LocalServer, stop: asyncio.Event, fail: Callable[[str], None]) -> AsyncIterator[Any]:
    if server.url is None:
        command, *args = server.argv
        if os.name == "nt":
            command = shutil.which(command) or ""
            if Path(command).suffix.lower() not in (".exe", ".com"):
                raise OSError(FAILED)
        with open(os.devnull, "w", encoding="utf-8") as errlog:
            async with stdio_client(StdioServerParameters(command=command, args=args), errlog=errlog) as (read, write):
                yield _WatchStdout(read, stop), write
        return
    transport = _LoopbackTransport(httpx2.URL(server.url), fail)
    async with httpx2.AsyncClient(
        transport=transport, trust_env=False, follow_redirects=False,
        timeout=httpx2.Timeout(5.0, read=None),
    ) as client:
        async with streamable_http_client(server.url, http_client=client, max_sse_event_size=2 * RESULT_LIMIT) as streams:
            yield streams


@dataclass
class _Connection:
    ready: asyncio.Future[ClientSession | None]
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] = field(init=False, repr=False)
    online: bool = False
    failure: str = FAILED

    def fail(self, reason: str) -> None:
        self.failure = reason
        self.stop.set()

    async def serve(self, server: LocalServer) -> None:
        async def on_message(message: types.ServerNotification | Exception) -> None:
            if isinstance(message, Exception):
                self.stop.set()

        try:
            async with _transport(server, self.stop, self.fail) as (read, write), ClientSession(read, write, message_handler=on_message) as session:
                initialize = asyncio.create_task(session.initialize())
                stopping = asyncio.create_task(self.stop.wait())
                try:
                    done, _ = await asyncio.wait((initialize, stopping), return_when=asyncio.FIRST_COMPLETED)
                    if stopping in done:
                        return
                    initialize.result()
                    self.online = True
                    self.ready.set_result(session)
                    await stopping
                finally:
                    self.online = False
                    for task in (initialize, stopping):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(initialize, stopping, return_exceptions=True)
        except Exception:
            pass
        finally:
            self.stop.set()
            if not self.ready.done():
                self.ready.set_result(None)

    async def session(self) -> ClientSession:
        session = await asyncio.shield(self.ready)
        if session is None or self.stop.is_set():
            raise CapabilityError(self.failure)
        return session


class MCPCapability(Capability):
    id = "mcp"
    version = "1"
    title = "Local MCP"
    actions = {
        "servers": ActionSpec("List configured local MCP servers and their connection state.", {
            "type": "object", "properties": {}, "additionalProperties": False,
        }),
        "tools": ActionSpec("List tools on a configured local MCP server.", {
            "type": "object", "properties": {"server": _SERVER}, "required": ["server"], "additionalProperties": False,
        }),
        "call": ActionSpec("Call a tool on a configured local MCP server.", {
            "type": "object", "properties": {
                "server": _SERVER, "tool": {"type": "string"}, "arguments": {"type": "object"},
            }, "required": ["server", "tool", "arguments"], "additionalProperties": False,
        }),
    }

    def __init__(self, servers: dict[str, LocalServer]):
        self.servers = servers
        self._connections: dict[str, _Connection] = {}
        self._locks = {name: asyncio.Lock() for name in servers}
        self._connected = False

    def available(self) -> str | None:
        return None if self.servers else "Add a local server with --mcp."

    async def run(self, ctx: CompanionContext) -> None:
        levels = [(logger := logging.getLogger(name), logger.level) for name in _QUIET_LOGGERS]
        for logger, _ in levels:
            logger.setLevel(logging.CRITICAL + 1)
        self._connected = True
        try:
            await asyncio.Future()
        finally:
            self._connected = False
            connections = self._connections
            self._connections = {}
            for connection in connections.values():
                connection.stop.set()
            try:
                await asyncio.gather(*(c.task for c in connections.values()))
            finally:
                for logger, level in levels:
                    logger.setLevel(level)

    async def invoke(self, action: str, args: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
        spec = self.actions.get(action)
        if not self._connected or spec is None or set(args) != spec.schema["properties"].keys():
            raise CapabilityError(FAILED)
        if action == "servers":
            states = await asyncio.gather(*(self._online(name, server) for name, server in self.servers.items()))
            return {"servers": [{"name": name, "kind": server.kind, "online": online}
                                for (name, server), online in zip(self.servers.items(), states)]}
        name, tool, arguments = args.get("server"), args.get("tool"), args.get("arguments")
        if not isinstance(name, str) or name not in self.servers:
            raise CapabilityError("Unknown local MCP server.")
        if action == "call" and (not isinstance(tool, str) or not tool or not isinstance(arguments, dict)):
            raise CapabilityError(FAILED)
        connection = None
        try:
            async with asyncio.timeout(CALL_TIMEOUT_S), self._locks[name]:
                connection = await self._connection(name)
                session = await connection.session()
                try:
                    if action == "tools":
                        return await self._tools(session)
                    result = await session.call_tool(tool, arguments)
                except CapabilityError:
                    raise
                except MCPError as error:
                    if action == "call" and error.code != types.CONNECTION_CLOSED and not connection.stop.is_set():
                        raise _tool_error(error.message) from None
                    connection.stop.set()
                    raise CapabilityError(connection.failure) from None
                except Exception:
                    connection.stop.set()
                    raise CapabilityError(connection.failure) from None
                if not isinstance(result, types.CallToolResult):
                    raise CapabilityError(FAILED)
                if result.is_error:
                    raise _tool_error(next((item.text for item in result.content if isinstance(item, types.TextContent)), ""))
                out = {"content": [item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in result.content],
                       "isError": False}
                if result.structured_content is not None:
                    out["structuredContent"] = result.structured_content
                if _json_size(out) > RESULT_LIMIT:
                    raise CapabilityError(TOO_LARGE)
                return out
        except TimeoutError:
            if connection is not None:
                connection.stop.set()
            raise CapabilityError(TIMED_OUT) from None
        except CapabilityError:
            raise
        except asyncio.CancelledError:
            if connection is not None:
                connection.stop.set()
            raise
        except Exception:
            raise CapabilityError(FAILED) from None

    async def _connection(self, name: str) -> _Connection:
        connection = self._connections.get(name)
        if connection is not None:
            if not connection.stop.is_set():
                return connection
            await asyncio.shield(connection.task)
        connection = _Connection(asyncio.get_running_loop().create_future())
        connection.task = asyncio.create_task(connection.serve(self.servers[name]))
        self._connections[name] = connection
        return connection

    async def _online(self, name: str, server: LocalServer) -> bool:
        current = self._connections.get(name)
        online = bool(current and current.online and not current.stop.is_set())
        if server.url is None or self._locks[name].locked():
            return online
        connection = None
        try:
            async with asyncio.timeout(PROBE_TIMEOUT_S), self._locks[name]:
                connection = await self._connection(name)
                session = await connection.session()
                if connection is current and online:
                    await session.send_ping()
                return True
        except asyncio.CancelledError:
            if connection is not None:
                connection.stop.set()
            raise
        except Exception:
            if connection is not None:
                connection.stop.set()
            return False

    async def _tools(self, session: ClientSession) -> dict[str, Any]:
        tools, cursors, size = [], set(), len('{"tools":[]}')
        cursor = None
        while True:
            page = await session.list_tools(params=types.PaginatedRequestParams(cursor=cursor) if cursor else None)
            for tool in page.tools:
                item = {"name": tool.name, "description": tool.description or "", "inputSchema": tool.input_schema}
                size += _json_size(item) + bool(tools)
                if size > RESULT_LIMIT:
                    raise CapabilityError(TOO_LARGE)
                tools.append(item)
            cursor = page.next_cursor
            if not cursor:
                return {"tools": tools}
            if cursor in cursors or len(cursors) >= 100:
                raise CapabilityError(FAILED)
            cursors.add(cursor)
