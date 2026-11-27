# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Modified for SwB.

"""One control session with a Muse VM.

Opens a WebSocket to ``/v1/noise`` with the per-VM bearer in an Authorization
header, runs the Noise XX handshake, then opens a long-lived ``POST
/api/gadget/control`` stream on the VM's Gadget API service. Both directions
of that stream carry JSON messages, each prefixed with its length as a
little-endian u32:

* device → VM: ``link.register`` with an empty command set;
* VM → device: the register reply and events such as ``link.unpaired``.

Messages the device sends to the Muse (:meth:`LinkSession.send_chat`) go as
separate ``POST /chat/stream`` requests on the same session, to the VM's
daemon like every request other than the control stream.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import struct
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Sequence
from urllib.parse import quote

from . import tls
from .muse_api import user_agent
from .noise import Header, NoiseTransport, NoiseXXInitiator

log = logging.getLogger(__name__)
_wire_log = logging.Logger(__name__ + ".wire")
_wire_log.disabled = True

NOISE_PATH = "/v1/noise"
CONTROL_PATH = "/api/gadget/control"
CONTROL_SERVICE = "gadget"
CHAT_PATH = "/chat/stream"
APP_ID = "musegadget"
REQUEST_TIMEOUT_S = 60
MAX_RESPONSE_BYTES = 1024 * 1024
HANDSHAKE_TIMEOUT_S = 20
PING_INTERVAL_S = 20
MAX_INBOUND_MESSAGE = 4 * 1024 * 1024
RECEIVE_FRAMES_PER_TURN = 32
RECEIVE_BYTES_PER_TURN = 64 * 1024
MAX_CHAT_EVENT_BYTES = 256 * 1024
MAX_STREAM_BUFFER_BYTES = 1024 * 1024
STREAM_CHUNK_BYTES = 16 * 1024
MAX_INLINE_REQUEST_BYTES = 64 * 1024
MAX_CONTROL_MESSAGE_BYTES = 256 * 1024
_URI_COMPONENT_SAFE = "-_.!~*'()"


class Outcome(enum.Enum):
    CLOSED = "closed"               # connection ended; reconnect normally
    AUTH_REJECTED = "auth_rejected"  # edge refused the VM bearer; re-fetch VMs
    FORBIDDEN = "forbidden"         # authenticated but not allowed right now
    UNPAIRED = "unpaired"           # the Muse removed this device
    STOPPED = "stopped"


@dataclass(frozen=True)
class DeviceDescription:
    node_id: str
    display_name: str
    version: str

    def register_message(self, request_id: str) -> dict:
        return {"type": "req", "id": request_id, "method": "link.register", "params": {
            "node_id": self.node_id,
            "display_name": self.display_name,
            "platform": "linux",
            "version": self.version,
            "device_family": "gadget",
            "model_id": "swb",
            "metadata": {"model_name": "SwB"},
            "is_wakeup_supported": False,
            "commands_v2": {},
        }}


def encode_message(obj: dict) -> bytes:
    data = json.dumps(obj, separators=(",", ":")).encode()
    return struct.pack("<I", len(data)) + data


class MessageDecoder:
    """Splits the control stream into length-prefixed JSON messages.

    A message may span body chunks, so bytes are buffered until complete.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[dict]:
        self._buf += data
        messages = []
        while len(self._buf) >= 4:
            (length,) = struct.unpack_from("<I", self._buf)
            if length > MAX_INBOUND_MESSAGE:
                raise ValueError(f"inbound message too large: {length}")
            if len(self._buf) < 4 + length:
                break
            raw = bytes(self._buf[4:4 + length])
            del self._buf[:4 + length]
            if not raw:
                continue  # keepalive
            try:
                message = json.loads(raw)
            except (ValueError, RecursionError):  # malformed, overdeep, or non-UTF-8 JSON
                log.warning("dropping malformed control message (%d bytes)", length)
                continue
            if isinstance(message, dict):
                messages.append(message)
        return messages


class WakeNotSent(ConnectionError):
    """The chat request failed before its first WebSocket frame."""


class LinkSession:
    def __init__(self, *, noise_host: str, vm_id: str, vm_auth_token: str,
                 device: DeviceDescription, connect=None) -> None:
        self._url = f"wss://{noise_host}{NOISE_PATH}?vm_id={quote(vm_id, safe=_URI_COMPONENT_SAFE)}"
        self._token = vm_auth_token
        self._device = device
        self._connect = connect
        self._send_lock = asyncio.Lock()
        self._stream_id = 0
        self._register_id = ""
        self._requests: dict[int, _Request] = {}
        self._streams: dict[int, HttpStream] = {}
        self._running = False
        self.registered = asyncio.Event()
        self._closed = False
        self._used = False
        self.registered_at: float | None = None

    @property
    def online(self) -> bool:
        return self._running and self.registered.is_set()

    async def run(self, stop: asyncio.Event) -> Outcome:
        if self._used:
            raise ValueError("a LinkSession can only run once")
        self._used = True
        self.registered.clear()
        ws = None
        reader = stopper = register = None
        try:
            try:
                ws = await self._open()
            except _UpgradeRejected as rejected:
                log.warning("VM refused connection: HTTP %d", rejected.status)
                return Outcome.AUTH_REJECTED if rejected.status == 401 else Outcome.FORBIDDEN
            self._ws = ws
            self._transport = await asyncio.wait_for(self._handshake(ws), HANDSHAKE_TIMEOUT_S)
            self._running = True
            await self._open_control_stream()
            reader = asyncio.create_task(self._read_loop())
            stopper = asyncio.create_task(stop.wait())
            register = asyncio.create_task(self.registered.wait())
            done, _ = await asyncio.wait({reader, stopper, register}, timeout=HANDSHAKE_TIMEOUT_S,
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                log.warning("Muse registration timed out")
                return Outcome.CLOSED
            if stopper in done:
                return Outcome.STOPPED
            if reader in done:
                return reader.result()
            done, _ = await asyncio.wait({reader, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if stopper in done:
                return Outcome.STOPPED
            return reader.result()
        finally:
            self._running = False
            self._closed = True
            self.registered.clear()
            tasks = [t for t in (reader, stopper, register) if t is not None]
            for task in tasks:
                task.cancel()
            for request in self._requests.values():
                request.fail(ConnectionError("session ended"))
            self._requests.clear()
            for stream in self._streams.values():
                stream.fail(ConnectionError("session ended"))
            self._streams.clear()
            # Close before joining writers stalled by network backpressure.
            if ws is not None:
                try:
                    await asyncio.wait_for(ws.close(), 2)
                except Exception:
                    log.warning("could not close Noise connection")
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _open(self):
        headers = {"Authorization": f"Bearer {self._token}"}
        if self._connect is not None:
            return await self._connect(self._url, headers)
        from websockets.asyncio.client import connect
        from websockets.exceptions import InvalidStatus

        class DirectConnect(connect):
            def process_redirect(self, exc: Exception) -> Exception:
                return exc

        try:
            return await DirectConnect(
                self._url,
                additional_headers=headers,
                user_agent_header=user_agent(),
                compression=None,
                open_timeout=HANDSHAKE_TIMEOUT_S,
                ping_interval=PING_INTERVAL_S,
                ping_timeout=PING_INTERVAL_S,
                close_timeout=2,
                max_size=128 * 1024,
                ssl=tls.client_context(),
                logger=_wire_log,
            )
        except InvalidStatus as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise _UpgradeRejected(status) from None
            raise

    async def _handshake(self, ws) -> NoiseTransport:
        initiator = NoiseXXInitiator()
        initiator.initialize()
        await ws.send(initiator.write_message1())
        msg2 = await ws.recv()
        if isinstance(msg2, str):
            raise ConnectionError("Noise handshake got a text frame")
        initiator.read_message2(bytes(msg2))
        # The bearer already authenticated us at the upgrade; message 3
        # carries an empty payload.
        await ws.send(initiator.write_message3())
        send, recv = initiator.split()
        log.info("Noise session established")
        return NoiseTransport(send, recv)

    async def _open_control_stream(self) -> None:
        encrypted = await self._send_encrypted(
            lambda: self._transport.start_stream_request(
                "POST", CONTROL_PATH, service=CONTROL_SERVICE),
        )
        self._stream_id = encrypted.stream_id
        self._register_id = str(uuid.uuid4())
        message = self._device.register_message(self._register_id)
        if len(encode_message(message)) > MAX_CONTROL_MESSAGE_BYTES:
            raise ValueError("link.register exceeds the VM's control message budget")
        await self._send_encrypted(lambda: self._transport.encrypt_body_chunk(
            self._stream_id, encode_message(message), service=CONTROL_SERVICE,
        ))

    async def send_chat(self, message: str, session_id: str) -> tuple[int, dict | None]:
        if not self.online:
            raise WakeNotSent("Muse session is not registered")
        body = json.dumps({
            "message": message,
            "output_modality": "text",
            "device_id": self._device.node_id,
            "session_id": session_id,
        }).encode()
        return await self._request(body)

    async def _request(self, body: bytes) -> tuple[int, dict | None]:
        request = _Request(asyncio.get_running_loop().create_future())
        stream_id = None
        headers = self._http_headers([Header("Content-Type", "application/json")])
        upload_in_parts = len(body) > MAX_INLINE_REQUEST_BYTES

        def encrypt():
            nonlocal stream_id
            if upload_in_parts:
                encrypted = self._transport.start_stream_request("POST", CHAT_PATH, headers=headers)
            else:
                encrypted = self._transport.encrypt_http_request("POST", CHAT_PATH, body, headers=headers)
            stream_id = encrypted.stream_id
            self._requests[stream_id] = request
            return encrypted

        completed = False
        try:
            await self._send_encrypted(encrypt, wake=True)
            if upload_in_parts:
                for offset in range(0, len(body), STREAM_CHUNK_BYTES):
                    part = body[offset:offset + STREAM_CHUNK_BYTES]
                    await self._send_encrypted(lambda: self._transport.encrypt_body_chunk(
                        stream_id, part, end_body=offset + len(part) == len(body),
                    ))
            status, response = await asyncio.wait_for(request.done, REQUEST_TIMEOUT_S)
            completed = True
        finally:
            if stream_id is not None:
                self._requests.pop(stream_id, None)
            if not request.done.done():
                request.done.cancel()
            elif not request.done.cancelled():
                request.done.exception()
            if not completed and stream_id is not None:
                await self._reset_stream(stream_id)
        try:
            decoded = json.loads(response) if response else None
        except (ValueError, RecursionError):
            decoded = None
        return status, decoded if isinstance(decoded, dict) else None

    @staticmethod
    def _http_headers(headers: Sequence[Header] = ()) -> list[Header]:
        return [
            *headers,
            Header("x-request-id", str(uuid.uuid4())),
            Header("x-app-id", APP_ID),
        ]

    @asynccontextmanager
    async def stream_http(
        self, method: str, path: str, body: bytes = b"", *, headers: Sequence[Header] = (),
        max_buffer_bytes: int = MAX_STREAM_BUFFER_BYTES,
    ) -> AsyncIterator[HttpStream]:
        """Open a bounded HTTP response stream; closing it cancels its request.

        The response status and headers are available on entry. Consumers
        iterate byte chunks without holding up the control stream. Pending
        chunks coalesce into 16 KiB buffers. A consumer that exceeds its byte
        budget fails independently; the default budget is 1 MiB.
        """
        if not self._running:
            raise ConnectionError("Muse session is not connected")
        stream = HttpStream(max_buffer_bytes)
        stream_id = None

        def encrypt():
            nonlocal stream_id
            encrypted = self._transport.encrypt_http_request(
                method, path, body, headers=self._http_headers(headers),
            )
            stream_id = encrypted.stream_id
            self._streams[stream_id] = stream
            return encrypted

        try:
            await self._send_encrypted(encrypt)
            await asyncio.wait_for(stream.ready, REQUEST_TIMEOUT_S)
            yield stream
        finally:
            if stream_id is not None:
                self._streams.pop(stream_id, None)
                if not stream.ended and not stream.reset_requested:
                    stream.reset_requested = True
                    await self._reset_stream(stream_id)
            stream.close()

    async def subscribe_chat(
        self, session_id: str, *, opened: asyncio.Event | None = None,
    ) -> AsyncIterator[dict]:
        """Yield Muse chat events from its NDJSON subscription.

        ``opened`` is set once Muse accepts this subscription, so events from
        then on reach it.
        """
        body = json.dumps({"session_id": session_id}).encode()
        path = "/chat/subscribe"
        async with self.stream_http("POST", path, body, headers=[
            Header("Content-Type", "application/json"), Header("Accept", "application/x-ndjson"),
        ]) as stream:
            if not 200 <= stream.status < 300:
                raise HttpStreamError(stream.status, path)
            if opened is not None:
                opened.set()
            buffered = bytearray()
            async for chunk in stream:
                for part in chunk.splitlines(keepends=True):
                    buffered += part
                    if len(buffered) > MAX_CHAT_EVENT_BYTES:
                        raise ValueError("Muse chat event exceeds 256 KiB")
                    if not buffered.endswith(b"\n"):
                        continue
                    event = self._chat_event(bytes(buffered))
                    buffered.clear()
                    if event is not None:
                        yield event
            if buffered and (event := self._chat_event(bytes(buffered))) is not None:
                yield event

    @staticmethod
    def _chat_event(raw: bytes) -> dict | None:
        if not raw.strip():
            return None
        try:
            event = json.loads(raw)
        except (ValueError, RecursionError):  # malformed, overdeep, or non-UTF-8 JSON
            log.warning("dropping malformed chat event (%d bytes)", len(raw))
            return None
        return event if isinstance(event, dict) and event.get("type") == "event" else None

    async def _reset_stream(self, stream_id: int) -> None:
        if not self._running:
            return
        try:
            async with asyncio.timeout(1):
                await self._send_encrypted(
                    lambda: self._transport.encrypt_reset(stream_id, reason="consumer closed"),
                )
        except Exception:
            log.debug("could not cancel closed Muse stream")

    async def _send_encrypted(self, encrypt, *, wake: bool = False):
        # Encryption advances the Noise nonce: acquire the lock BEFORE encrypting
        # so concurrent requests and cancelled sends cannot reorder ciphertext.
        async with self._send_lock:
            if wake and not self.online:
                raise WakeNotSent("Muse session is not registered")
            if self._closed:
                raise ConnectionError("session closed")
            try:
                result = encrypt()
                frames = result.frames if hasattr(result, "frames") else result
                async with asyncio.timeout(HANDSHAKE_TIMEOUT_S):
                    for frame in frames:
                        await self._ws.send(frame)
                return result
            except BaseException:
                # Delivery is now ambiguous; never reuse this cipher state.
                self._closed = True
                self._running = False
                for request in self._requests.values():
                    request.fail(ConnectionError("send failed"))
                for stream in self._streams.values():
                    stream.fail(ConnectionError("send failed"))
                try:
                    await asyncio.wait_for(self._ws.close(), 2)
                except Exception:
                    log.warning("could not close failed Noise connection")
                raise

    # -- Receiving ------------------------------------------------------------

    async def _read_loop(self) -> Outcome:
        decoder = MessageDecoder()
        received_frames = received_bytes = 0
        while True:
            if (received_frames >= RECEIVE_FRAMES_PER_TURN
                    or received_bytes >= RECEIVE_BYTES_PER_TURN):
                # A buffered recv() may not yield to response consumers or writers.
                await asyncio.sleep(0)
                received_frames = received_bytes = 0
            try:
                raw = await self._ws.recv()
            except Exception:
                log.info("Muse control connection closed")
                return Outcome.CLOSED
            received_frames += 1
            received_bytes += len(raw)
            if isinstance(raw, str):
                log.warning("ignoring text frame on Noise connection")
                continue
            frame = self._transport.decrypt_frame(bytes(raw))
            if frame is None:
                continue
            if frame.stream_id != self._stream_id:
                stream = self._streams.get(frame.stream_id)
                if stream is not None:
                    if not stream.on_frame(frame):
                        stream.reset_requested = True
                        await self._reset_stream(frame.stream_id)
                elif (request := self._requests.get(frame.stream_id)) is not None:
                    request.on_frame(frame)
                continue
            if frame.kind == "reset":
                log.warning("Muse control stream reset")
                return Outcome.CLOSED
            if frame.kind == "response":
                if not 200 <= frame.value.status < 300:
                    log.warning("%s refused: HTTP %d", CONTROL_PATH, frame.value.status)
                    return Outcome.FORBIDDEN if frame.value.status == 403 else Outcome.CLOSED
                data, ended = frame.value.body, frame.value.end_body
            else:
                data, ended = frame.value.data, frame.value.end_body
            for message in decoder.feed(data):
                outcome = self._handle(message)
                if outcome is not None:
                    return outcome
            if ended:
                log.info("control stream ended by VM")
                return Outcome.CLOSED

    def _handle(self, message: dict) -> Outcome | None:
        if not self.registered.is_set() and message.get("id") == self._register_id and message.get("method") is None:
            if message.get("error") or message.get("ok") is False:
                log.error("link.register rejected")
                return Outcome.FORBIDDEN
            self.registered_at = time.monotonic()
            self.registered.set()
            log.info("registered with Muse")
            return None
        event = message.get("event")
        if event in ("link.unpaired", "node.unpaired"):
            log.warning("the Muse removed this device")
            return Outcome.UNPAIRED
        return None

class HttpStreamError(ConnectionError):
    """Muse refused an HTTP request carried by the Noise session."""

    def __init__(self, status: int, path: str) -> None:
        super().__init__(f"Muse request {path} failed: HTTP {status}")
        self.status = status
        self.path = path


class HttpStream:
    """Response headers and bounded byte chunks dispatched by the reader."""

    def __init__(self, max_buffer_bytes: int = MAX_STREAM_BUFFER_BYTES) -> None:
        self.ready = asyncio.get_running_loop().create_future()
        self.status = 0
        self.headers: list[Header] = []
        self.ended = False
        self.reset_requested = False
        self._finished = False
        self._chunks: deque[bytearray] = deque()
        self._available = asyncio.Event()
        self._error: Exception | None = None
        self._buffered = 0
        self._max_buffer_bytes = max_buffer_bytes

    def on_frame(self, frame) -> bool:
        if self._finished:
            return True
        if frame.kind == "reset":
            self.ended = True
            self.fail(ConnectionError("Muse response stream reset"))
            return True
        if frame.kind == "response":
            if self.ready.done() or not 100 <= frame.value.status <= 599:
                self.fail(ConnectionError("Muse returned an invalid or duplicate response head"))
                return False
            self.status = frame.value.status
            self.headers = list(frame.value.headers)
            if not self.ready.done():
                self.ready.set_result(None)
            data, ended = frame.value.body, frame.value.end_body
        else:
            if not self.ready.done():
                self.fail(ConnectionError("Muse response body arrived without headers"))
                return False
            data, ended = frame.value.data, frame.value.end_body
        if data:
            if self._buffered + len(data) > self._max_buffer_bytes:
                self.fail(BufferError("Muse response consumer fell behind"))
                return False
            self._buffered += len(data)
            offset = 0
            if self._chunks and len(self._chunks[-1]) < STREAM_CHUNK_BYTES:
                take = min(len(data), STREAM_CHUNK_BYTES - len(self._chunks[-1]))
                self._chunks[-1].extend(data[:take])
                offset = take
            while offset < len(data):
                self._chunks.append(bytearray(data[offset:offset + STREAM_CHUNK_BYTES]))
                offset += STREAM_CHUNK_BYTES
            self._available.set()
        if ended:
            self.ended = self._finished = True
            self._available.set()
        return True

    def fail(self, error: Exception) -> None:
        if self._finished:
            return
        self._finished = True
        if not self.ready.done():
            self.ready.set_exception(error)
        self._drain()
        self._error = error
        self._available.set()

    def _drain(self) -> None:
        self._chunks.clear()
        self._buffered = 0

    def close(self) -> None:
        if not self.ready.done():
            self.ready.cancel()
        elif not self.ready.cancelled():
            self.ready.exception()
        self._finished = True
        self._error = None
        self._drain()
        self._available.set()

    def __aiter__(self) -> HttpStream:
        return self

    async def __anext__(self) -> bytes:
        while True:
            if self._chunks:
                chunk = self._chunks.popleft()
                self._buffered -= len(chunk)
                return bytes(chunk)
            if self._error is not None:
                raise self._error
            if self._finished:
                raise StopAsyncIteration
            self._available.clear()
            await self._available.wait()


class _Request:
    """Collects the response to one request stream."""

    def __init__(self, done: asyncio.Future) -> None:
        self.done = done
        self.status = 0
        self.body = bytearray()

    def fail(self, error: Exception) -> None:
        if not self.done.done():
            self.done.set_exception(error)

    def on_frame(self, frame) -> None:
        if self.done.done():
            return
        if frame.kind == "reset":
            self.done.set_exception(ConnectionError("Muse response stream reset"))
            return
        if frame.kind == "response":
            if self.status or not 100 <= frame.value.status <= 599:
                self.fail(ValueError("invalid or duplicate response head"))
                return
            self.status = frame.value.status
            data, ended = frame.value.body, frame.value.end_body
        elif frame.kind == "body_chunk" and self.status:
            data, ended = frame.value.data, frame.value.end_body
        else:
            self.fail(ValueError("response body before head"))
            return
        if len(self.body) + len(data) > MAX_RESPONSE_BYTES:
            self.done.set_exception(ValueError("response too large"))
            return
        self.body += data
        if ended:
            self.done.set_result((self.status, bytes(self.body)))


class _UpgradeRejected(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status
