from __future__ import annotations

import asyncio
import base64
import ctypes
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import stat
import tempfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from ..vendor.musegadget.ble_framing import CHUNK_STAGGER_S, DEFAULT_ATT_MTU, MAX_MESSAGE_BYTES, MAX_PACKET_BYTES
from .caps.base import CapabilityError
from .macos_app import installed_app
from .state import CompanionError

_MAX_RECORD = 8192
_PERMISSIONS = {"Allowed", "Denied", "Restricted", "Approval required", "Unavailable"}
_LOG = logging.getLogger(__name__)


class Channel:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, key: bytes) -> None:
        self.reader = reader
        self.writer = writer
        self.key = key
        self.sent = self.received = 0

    async def send(self, action: str, **fields: str) -> None:
        self.sent += 1
        data = json.dumps({"seq": self.sent, "type": action, **fields}, separators=(",", ":")).encode()
        if len(data) + 66 > _MAX_RECORD:
            raise CapabilityError("The Bluetooth helper request is too large.")
        mac = hmac.new(self.key, b"py:" + data, hashlib.sha256).hexdigest().encode("ascii")
        self.writer.write(mac + b" " + data + b"\n")
        await asyncio.wait_for(self.writer.drain(), 5)

    async def receive(self) -> dict:
        try:
            line = await self.reader.readline()
            if not line or len(line) > _MAX_RECORD or line[64:65] != b" " or not line.endswith(b"\n"):
                raise ValueError
            message = line[65:-1]
            mac = hmac.new(self.key, b"app:" + message, hashlib.sha256).hexdigest().encode("ascii")
            if not hmac.compare_digest(line[:64], mac):
                raise ValueError
            data = json.loads(message)
            if (not isinstance(data, dict) or type(data.get("seq")) is not int
                    or data["seq"] != self.received + 1 or not isinstance(data.get("type"), str)):
                raise ValueError
        except (OSError, ValueError, RecursionError):
            raise CapabilityError("The Bluetooth helper connection closed or failed authentication.") from None
        self.received += 1
        return data


def _same_user(writer: asyncio.StreamWriter) -> bool:
    uid = ctypes.c_uint()
    gid = ctypes.c_uint()
    conn = writer.get_extra_info("socket")
    return bool(
        conn is not None
        and ctypes.CDLL(None).getpeereid(conn.fileno(), ctypes.byref(uid), ctypes.byref(gid)) == 0
        and uid.value == os.getuid()
    )


@asynccontextmanager
async def helper(app: Path):
    support = Path.home() / "Library" / "Application Support" / "SwB"
    support.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = support.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise CompanionError("Set the SwB Application Support folder permissions to 0700.")
    with tempfile.TemporaryDirectory(prefix="ble-", dir=support) as directory:
        root = Path(directory)
        sock = root / "s"
        if len(os.fsencode(sock)) >= 104:
            raise CompanionError("The SwB Application Support path is too long for Bluetooth.")
        request = root / "bootstrap"
        key = secrets.token_bytes(32)
        server_fd = os.open(request, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(server_fd, "w", encoding="utf-8") as stream:
            json.dump({"socket": str(sock), "token": key.hex()}, stream)
        connected = asyncio.get_running_loop().create_future()
        closing = False

        def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            if closing or connected.done() or not _same_user(writer):
                writer.close()
                return
            connected.set_result(Channel(reader, writer, key))

        server = await asyncio.start_unix_server(accept, str(sock), limit=_MAX_RECORD)
        process = None
        try:
            sock.chmod(0o600)
            process = await asyncio.create_subprocess_exec(
                "/usr/bin/open", "-n", "-W", "-a", str(app), "--args", str(request),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            channel = await asyncio.wait_for(asyncio.shield(connected), 12)
            if (await asyncio.wait_for(channel.receive(), 5))["type"] != "ready":
                raise CapabilityError("The Bluetooth helper did not start.")
            yield channel
        except (OSError, TimeoutError):
            raise CapabilityError("The Bluetooth helper did not respond.") from None
        finally:
            closing = True
            server.close()
            if connected.done() and not connected.cancelled():
                writer = connected.result().writer
                writer.close()
                with suppress(OSError, TimeoutError):
                    await asyncio.wait_for(writer.wait_closed(), 2)
            with suppress(TimeoutError):
                await asyncio.wait_for(server.wait_closed(), 2)
            if process is not None and process.returncode is None:
                with suppress(ProcessLookupError, TimeoutError):
                    try:
                        await asyncio.wait_for(process.wait(), 2)
                    except TimeoutError:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), 2)


async def permission(*, authorize: bool = False) -> str:
    async with helper(installed_app()) as channel:
        await channel.send("authorize" if authorize else "status")
        data = await asyncio.wait_for(channel.receive(), 65 if authorize else 5)
        if data["type"] == "status" and isinstance(value := data.get("permission"), str) and value in _PERMISSIONS:
            return value
        raise CapabilityError("The Bluetooth helper could not check permission.")


class MacTransport:
    def __init__(self, name: str) -> None:
        self.name = name
        self.connection = 0
        self._mtu = DEFAULT_ATT_MTU
        self.task: asyncio.Task | None = None
        self.manager = None
        self.channel: Channel | None = None

    async def start(self, controller) -> None:
        self.controller = controller
        try:
            app = installed_app()
        except CompanionError:
            raise CapabilityError("Update the SwB Companion app with swb companion service install or swb companion run.") from None
        manager = helper(app)
        self.channel = await manager.__aenter__()
        self.manager = manager
        await self.channel.send("start", name=self.name)
        event = await asyncio.wait_for(self.channel.receive(), 15)
        if event["type"] != "started":
            raise CapabilityError("SwB Companion could not start Bluetooth advertising.")
        self.task = asyncio.create_task(self._events())

    async def _events(self) -> None:
        while True:
            event = await self.channel.receive()
            match event["type"]:
                case "mtu":
                    value = event.get("mtu")
                    if (not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{1,2}", value)
                            or not DEFAULT_ATT_MTU <= int(value) <= MAX_PACKET_BYTES + 3):
                        raise CapabilityError("The Bluetooth helper returned an invalid notification size.")
                    self._mtu = int(value)
                    _LOG.debug("Muse BLE subscribed (ATT MTU used for framing: %d)", self._mtu)
                case "packet":
                    value = event.get("packet")
                    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", value):
                        raise CapabilityError("The Bluetooth helper returned an invalid packet.")
                    try:
                        packet = base64.b64decode(value, validate=True)
                    except ValueError:
                        raise CapabilityError("The Bluetooth helper returned an invalid packet.") from None
                    if not packet or len(packet) > MAX_MESSAGE_BYTES:
                        raise CapabilityError("The Bluetooth helper returned an invalid packet.")
                    self.controller.on_write(packet)
                case "disconnect":
                    self.connection += 1
                    self._mtu = DEFAULT_ATT_MTU
                    _LOG.debug("Muse BLE unsubscribed")
                    self.controller.on_disconnect()
                case _:
                    raise CapabilityError("SwB Companion stopped Bluetooth advertising.")

    async def run(self) -> None:
        pairing = asyncio.create_task(self.controller.run())
        try:
            done, _ = await asyncio.wait((pairing, self.task), return_when=asyncio.FIRST_COMPLETED)
            if pairing not in done:
                await self.task
                raise CapabilityError("SwB Companion stopped Bluetooth advertising.")
            await pairing
        finally:
            if not pairing.done():
                pairing.cancel()
                with suppress(asyncio.CancelledError):
                    await pairing

    def mtu(self) -> int:
        return self._mtu

    async def send_packets(self, packets: list[bytes]) -> None:
        connection = self.connection
        for packet in packets:
            if self.channel is None or connection != self.connection:
                return
            await self.channel.send("send", packet=base64.b64encode(packet).decode("ascii"))
            await asyncio.sleep(CHUNK_STAGGER_S)

    async def disconnect(self, delay: float) -> None:
        await asyncio.sleep(delay)
        self.connection += 1
        self.controller.on_disconnect()

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError, CapabilityError):
                await self.task
        if self.channel is not None:
            with suppress(CapabilityError, OSError, TimeoutError):
                await self.channel.send("stop")
        if self.manager is not None:
            await self.manager.__aexit__(None, None, None)
        self.channel = None
