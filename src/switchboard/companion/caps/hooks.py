from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from ..harness import Session, harness
from ..hook import LINE_LIMIT, SOCKET_TIMEOUT, event_payload, event_state, socket_path, supported
from .base import ActionSpec, Capability, CapabilityError, CompanionContext

_PENDING_LIMIT = 64


@contextmanager
def _listener(path: Path) -> Iterator[socket.socket]:
    import fcntl

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = os.open(path.parent, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    try:
        if os.fstat(directory).st_uid != os.getuid():
            raise PermissionError("Choose a private companion settings directory.")
        fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.fchmod(directory, 0o700)
        try:
            previous = path.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(previous.st_mode) or previous.st_uid != os.getuid():
                raise PermissionError("Choose a private companion socket.")
            path.unlink()

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            try:
                path.chmod(0o600)
                yield listener
            finally:
                path.unlink(missing_ok=True)
    finally:
        os.close(directory)


class HooksCapability(Capability):
    id = "hooks"
    version = "1"
    title = "Local agent hooks"
    actions: dict[str, ActionSpec] = {}

    def available(self) -> str | None:
        return None if supported() else "Local agent hooks require Linux or macOS."

    async def run(self, ctx: CompanionContext) -> None:
        if not supported():
            return
        events: asyncio.Queue[tuple[float, dict[str, str]]] = asyncio.Queue(maxsize=_PENDING_LIMIT)
        readers: dict[asyncio.Task[None], asyncio.StreamWriter] = {}

        async def receive(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                line = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=SOCKET_TIMEOUT)
                received_at = time.monotonic()
                if len(line) <= LINE_LIMIT:
                    value = json.loads(line.decode("utf-8"))
                    if isinstance(value, dict) and (payload := event_payload(value.get("harness"), value)):
                        events.put_nowait((received_at, payload))
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.QueueFull,
                    OSError, ValueError, RecursionError):
                pass
            finally:
                writer.close()

        def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            if len(readers) >= _PENDING_LIMIT:
                writer.close()
                return
            task = asyncio.create_task(receive(reader, writer))
            readers[task] = writer
            task.add_done_callback(readers.pop)

        with _listener(socket_path()) as listener:
            server = await asyncio.start_unix_server(accept, sock=listener, limit=LINE_LIMIT)
            try:
                while True:
                    received_at, payload = await events.get()
                    agent, session, event = (payload[key] for key in ("harness", "session_id", "hook_event_name"))
                    state = event_state(agent, event, payload.get("notification_type", "")) or "unknown"
                    with suppress(CapabilityError):
                        await harness().report(ctx, self.id, Session(
                            agent, session, agent=agent, dir=payload.get("dir", ""), state=state,
                            detail="API error" if event == "StopFailure" else "",
                        ))
                    if text := payload.get("text"):
                        keys = [f"{agent}:{session}"]
                        if pane := payload.get("pane_id"):
                            keys.append(f"herdr:{pane}")
                        with suppress(CapabilityError):
                            await harness().reply(ctx, self.id, keys, text, received_at=received_at, expected_agent=agent)
            finally:
                server.close()
                for task, writer in readers.items():
                    writer.close()
                    task.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
                await server.wait_closed()
