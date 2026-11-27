import asyncio
import json
import logging
import time
import urllib.error
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from switchboard import muse_gadget
from switchboard.muse_gadget import MuseGadget, WakeDeliveryUnknown, WakeRejected, WakeUnavailable
from switchboard.vendor.musegadget import muse_api
from switchboard.vendor.musegadget import link_client
from switchboard.vendor.musegadget.identity import Identity
from switchboard.vendor.musegadget.link_client import MessageDecoder, encode_message
from switchboard.vendor.musegadget.noise import (
    ApplicationResponse, BodyChunk, NoiseFrameDecoder, NoiseXXResponder, ServiceFrame, ServiceType,
    decode_service_frame, decode_service_request, encode_noise_frames,
)
from switchboard.vendor.musegadget.noise.transport import encode_response_envelope


class Peer:
    def __init__(self, *, auto_register=True, existing=False, subscribe_status=None, wake_status=200):
        self.noise = NoiseXXResponder()
        self.noise.initialize()
        self.handshake = 0
        self.incoming = asyncio.Queue()
        self.decoder = NoiseFrameDecoder()
        self.closed = False
        self.control = None
        self.registered = asyncio.Event()
        self.registration = None
        self.auto_register = auto_register
        self.existing = existing
        self.subscribe_status = subscribe_status
        self.wake_status = wake_status
        self.requests = []
        self.uploads = {}
        self.posts = {}
        self.subscriptions = {}

    async def recv(self):
        frame = await self.incoming.get()
        if frame is None:
            raise ConnectionError("fixture-private-remote-reason")
        return frame

    async def close(self):
        if not self.closed:
            self.closed = True
            self.incoming.put_nowait(None)

    async def send(self, data):
        if self.closed:
            raise ConnectionError("fixture-private-remote-reason")
        if self.handshake == 0:
            self.incoming.put_nowait(self.noise.read_message1_and_write_message2(data))
            self.handshake += 1
            return
        if self.handshake == 1:
            self.noise.read_message3(data)
            self.tx, self.rx = self.noise.split()
            self.handshake += 1
            return
        assembled = self.decoder.decode(self.rx.decrypt_with_ad(b"", data))
        if assembled is None:
            return
        envelope = decode_service_request(assembled)
        frame = decode_service_frame(envelope.payload)
        stream = frame.stream_id
        if frame.kind == "reset":
            return
        if frame.kind == "body_chunk" and stream == self.control:
            assert envelope.service == ServiceType.SERVICE_GADGET
            messages = MessageDecoder().feed(frame.value.data)
            self.registration, = messages
            self.registered.set()
            if self.auto_register:
                self.accept()
            return
        if frame.kind == "request" and frame.value.path == "/api/gadget/control":
            assert envelope.service == ServiceType.SERVICE_GADGET
            assert frame.value.verb == "POST" and not frame.value.end_body
            self.control = stream
            self.response(stream, 200, end=False)
            return
        assert envelope.service == ServiceType.SERVICE_DAEMON
        if frame.kind == "request":
            assert frame.value.verb == "POST"
            assert {header.key.lower() for header in frame.value.headers} >= {"x-app-id", "x-request-id"}
            self.requests.append(frame.value.path)
            if not frame.value.end_body:
                self.uploads[stream] = bytearray(frame.value.body)
                return
            path, body = frame.value.path, json.loads(frame.value.body)
        else:
            self.uploads[stream] += frame.value.data
            if not frame.value.end_body:
                return
            path, body = "/chat/stream", json.loads(self.uploads.pop(stream))
        sid = body["session_id"]
        if path == "/chat/subscribe":
            if self.subscribe_status is not None:
                self.response(stream, self.subscribe_status)
            elif sid not in self.posts and not self.existing:
                self.response(stream, 404)
            else:
                self.subscriptions[sid] = stream
                self.response(stream, 200, end=False)
                if sid in self.posts:
                    self.reply(sid)
        else:
            assert path == "/chat/stream"
            self.posts[sid] = body
            if self.wake_status is None:
                await self.close()
            elif self.wake_status == 200:
                if sid in self.subscriptions:
                    self.reply(sid)
                self.response(stream, 200, {"result": {"message_id": "user", "session_id": sid}})
            else:
                self.response(stream, self.wake_status)

    def push(self, frame):
        for chunk in encode_noise_frames(encode_response_envelope(frame)):
            self.incoming.put_nowait(self.tx.encrypt_with_ad(b"", chunk))

    def response(self, stream, status, payload=None, *, end=True):
        body = json.dumps(payload).encode() if payload is not None else b""
        self.push(ServiceFrame.response(stream, ApplicationResponse(status=status, body=body, end_body=end)))

    def control_message(self, payload):
        self.push(ServiceFrame.body_chunk(self.control, BodyChunk(data=encode_message(payload))))

    def accept(self):
        self.control_message({"type": "res", "id": self.registration["id"], "ok": True})

    def reply(self, sid):
        def event(name, seq, **payload):
            return {"type": "event", "event": name, "seq": seq, "payload": payload}

        events = [
            event("delta.text_append", 20, session_id="different-chat", message_id="foreign", text="wrong"),
            event("message.user", 2, session_id=sid, message_id="user"),
            event("delta.text_append", 20, session_id=sid, message_id="answer",
                  parent_message_id="user", text="Pa"),
            event("delta.text_append", 20, session_id=sid, message_id="answer", text="duplicate"),
            event("delta.text_append", 21, session_id=sid, message_id="answer", text="rtial"),
            event("message.assistant", 3, session_id=sid, message_id="answer", display_text="Pong"),
            event("task.status", 22, session_id=sid, status="completed"),
        ]
        body = ("\n".join(json.dumps(item) for item in events) + "\n").encode()
        stream = self.subscriptions[sid]
        self.push(ServiceFrame.body_chunk(stream, BodyChunk(data=body[:45])))
        self.push(ServiceFrame.body_chunk(stream, BodyChunk(data=body[45:])))


def make_gadget(monkeypatch, peers, *, pairing=None, fetch=None, refresh=None):
    monkeypatch.setattr(muse_gadget, "BACKOFF_BASE_S", 0.005)
    monkeypatch.setattr(muse_gadget, "BACKOFF_MAX_S", 0.02)
    records, connections, lookups = [], [], []
    pair = {"node_id": "gadget-abcdef", "access_token": "access-fixture", "refresh_token": "refresh-fixture",
            "access_token_saved_at": time.time(), **(pairing or {})}
    identity = Identity("02:00:00:ab:cd:ef")

    def fetch_vms(token, root):
        lookups.append((token, root))
        if fetch:
            rejected = fetch(token)
            if rejected is not None:
                return rejected
        return [{"vm_id": "side-vm", "vm_auth_token": "not-default", "is_default": False},
                {"vm_id": "vm fixture", "vm_auth_token": "fixture-vm-bearer", "is_default": True}], 200

    async def connect(url, headers):
        assert url == "wss://hatch.metaaivm.com/v1/noise?vm_id=vm%20fixture"
        assert headers == {"Authorization": "Bearer fixture-vm-bearer"}
        connections.append(peers[len(connections)])
        return connections[-1]

    monkeypatch.setattr(muse_api, "fetch_vms_with_status", fetch_vms)
    if refresh:
        monkeypatch.setattr(muse_api, "refresh_device_token", refresh)
    gadget = MuseGadget(identity=identity, pairing=pair, sdk_token="sdk-fixture",
                        save_pairing=records.append, connect=connect)
    return gadget, records, connections, lookups


@asynccontextmanager
async def running(gadget):
    runner = asyncio.create_task(gadget.run())
    try:
        yield runner
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)


@pytest.mark.parametrize("existing", [True, False])
async def test_register_then_collect_isolated_side_chat_replies(monkeypatch, existing):
    peer = Peer(auto_register=False, existing=existing)
    gadget, _, _, _ = make_gadget(monkeypatch, [peer])
    async with asyncio.timeout(2), running(gadget):
        await peer.registered.wait()
        assert not gadget.online
        assert peer.registration["method"] == "link.register"
        params = peer.registration["params"]
        assert params["node_id"] == "gadget-abcdef"
        assert params["platform"] == "linux"
        assert params["device_family"] == "gadget"
        assert params["commands_v2"] == {}
        peer.accept()
        await gadget.wait_ready()
        assert gadget.online

        ids = [str(uuid4()), str(uuid4())]
        long_text = "🙂" * 20_000
        replies = await asyncio.gather(gadget.wake(ids[0], "ping"), gadget.wake(ids[1], long_text))
        assert replies == ["Pong", "Pong"]
        assert peer.posts == {
            sid: {"session_id": sid, "message": text, "device_id": "gadget-abcdef", "output_modality": "text"}
            for sid, text in zip(ids, ("ping", long_text), strict=True)
        }
        assert peer.requests.count("/chat/stream") == 2
        assert peer.requests.count("/chat/subscribe") == (2 if existing else 4)


async def test_reconnect_after_a_definitely_unsent_wake(monkeypatch):
    failed, recovered = Peer(subscribe_status=503), Peer()
    gadget, _, connections, _ = make_gadget(monkeypatch, [failed, recovered])
    async with asyncio.timeout(2), running(gadget):
        await gadget.wait_ready()
        sid = str(uuid4())
        with pytest.raises(WakeUnavailable):
            await gadget.wake(sid, "ping")
        assert not gadget.online
        assert not failed.posts
        await gadget.wait_ready()
        assert connections == [failed, recovered]
        assert await gadget.wake(sid, "ping") == "Pong"


async def test_disconnected_wake_is_unknown_and_is_never_replayed(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    failed, recovered = Peer(wake_status=None), Peer()
    gadget, _, connections, _ = make_gadget(monkeypatch, [failed, recovered])
    async with asyncio.timeout(2), running(gadget):
        await gadget.wait_ready()
        sid = str(uuid4())
        with pytest.raises(WakeDeliveryUnknown):
            await gadget.wake(sid, "fixture-private-message")
        await recovered.registered.wait()
        await gadget.wait_ready()
        assert connections == [failed, recovered]
        assert list(failed.posts) == [sid]
        assert not recovered.posts
    assert "fixture-private-remote-reason" not in caplog.text
    assert "fixture-private-message" not in caplog.text
    assert "fixture-vm-bearer" not in caplog.text


@pytest.mark.parametrize(("status", "error"), [(400, WakeRejected), (503, WakeDeliveryUnknown)])
async def test_http_wake_failures_are_not_requeued(monkeypatch, status, error):
    peer = Peer(wake_status=status)
    gadget, _, _, _ = make_gadget(monkeypatch, [peer])
    async with asyncio.timeout(2), running(gadget):
        await gadget.wait_ready()
        with pytest.raises(error):
            await gadget.wake(str(uuid4()), "ping")
        assert len(peer.posts) == 1


async def test_acknowledged_wake_without_a_complete_reply(monkeypatch):
    peer = Peer()
    peer.reply = lambda sid: None
    gadget, _, _, _ = make_gadget(monkeypatch, [peer])
    async with asyncio.timeout(2), running(gadget):
        await gadget.wait_ready()
        assert await gadget.wake(str(uuid4()), "ping", timeout=0.05) is None
        assert len(peer.posts) == 1


@pytest.mark.parametrize("expired", [True, False])
async def test_refresh_rotates_credentials_on_age_or_401(monkeypatch, expired):
    rotations = []

    def rotate(refresh, node, root, sdk):
        rotations.append((refresh, node, root, sdk))
        return {"access_token": "access-new", "refresh_token": "refresh-new"}, 200

    def fetch(token):
        return ([], 401) if token == "access-fixture" else None

    pair = {"access_token_saved_at": 1} if expired else {}
    peer = Peer()
    gadget, saved, _, lookups = make_gadget(monkeypatch, [peer], pairing=pair, refresh=rotate, fetch=fetch)
    async with asyncio.timeout(2), running(gadget) as runner:
        await gadget.wait_ready()
        assert rotations == [("refresh-fixture", "gadget-abcdef", "https://api.muse.ai", "sdk-fixture")]
        expected = ["access-new"] if expired else ["access-fixture", "access-new"]
        assert [token for token, _ in lookups] == expected
        assert len(saved) == 1
        assert saved[0]["access_token"] == "access-new"
        assert saved[0]["refresh_token"] == "refresh-new"
        assert saved[0]["access_token_saved_at"] > 1
        assert saved[0]["node_id"] == "gadget-abcdef"
        peer.control_message({"type": "event", "event": "link.unpaired"})
        await runner
    assert saved[-1] is None


async def test_refresh_rejection_removes_pairing_before_connect(monkeypatch):
    gadget, saved, connections, lookups = make_gadget(
        monkeypatch, [], pairing={"access_token_saved_at": 1}, refresh=lambda *_: (None, 401),
    )
    await asyncio.wait_for(gadget.run(), 1)
    assert saved == [None]
    assert not gadget.online
    assert not connections and not lookups


async def test_transient_refresh_failure_keeps_working_access_token(monkeypatch):
    gadget, saved, _, lookups = make_gadget(
        monkeypatch, [Peer()], pairing={"access_token_saved_at": 1}, refresh=lambda *_: (None, 503),
    )
    async with asyncio.timeout(2), running(gadget):
        await gadget.wait_ready()
        assert lookups == [("access-fixture", "https://api.muse.ai")]
        assert not saved


async def test_connection_stays_online_while_device_credentials_rotate(monkeypatch):
    monkeypatch.setattr(muse_gadget, "TOKEN_REFRESH_AGE_S", 0.25)
    peer = Peer()
    gadget, saved, connections, _ = make_gadget(
        monkeypatch, [peer], refresh=lambda *_: ({"access_token": "new", "refresh_token": "new"}, 200),
    )
    async with asyncio.timeout(2), running(gadget):
        await gadget.wait_ready()
        assert not saved
        while not saved:
            await asyncio.sleep(0.01)
        assert gadget.online
        assert connections == [peer]


def test_api_keeps_secrets_out_of_logs_and_refresh_uses_sdk_authorization(monkeypatch, caplog):
    requests = []

    def request(req):
        requests.append(req)
        if req.method == "GET":
            return 200, {"error_title": "fixture-private-error", "backend_error_code": "fixture-private-code"}
        raise urllib.error.URLError("fixture-private-reason")

    monkeypatch.setattr(muse_api, "_request", request)
    assert muse_api.fetch_vms_with_status("fixture-access") == ([], 200)
    assert muse_api.refresh_device_token(
        "hatch_refresh:fixture-refresh", "gadget-abcdef", sdk_token="fixture-sdk",
    ) == (None, None)
    assert requests[0].full_url == "https://api.muse.ai/fetch_vms"
    assert requests[0].get_header("Authorization") == "Bearer fixture-access"
    assert requests[1].full_url == "https://api.muse.ai/device_token/refresh"
    assert requests[1].get_header("Authorization") == "Bearer hatch_refresh:fixture-refresh"
    assert json.loads(requests[1].data) == {"device_id": "gadget-abcdef", "sdk_token": "fixture-sdk"}
    assert "fixture-" not in caplog.text


async def test_websocket_upgrade_disables_redirects_and_wire_log(monkeypatch, caplog):
    from websockets.asyncio import client

    class Connect:
        def __init__(self, url, *, logger, additional_headers, **options):
            self.logger = logger
            self.headers = additional_headers

        def __await__(self):
            async def open_socket():
                error = ConnectionError("https://elsewhere.example")
                assert self.process_redirect(error) is error
                assert self.logger.disabled
                self.logger.debug("wire authorization: %s", self.headers["Authorization"])
                return socket

            return open_socket().__await__()

    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(client, "connect", Connect)
    monkeypatch.setattr(link_client.tls, "client_context", lambda: None)
    socket = object()
    session = link_client.LinkSession(
        noise_host="hatch.metaaivm.com", vm_id="vm", vm_auth_token="fixture-vm-bearer",
        device=link_client.DeviceDescription("gadget-abcdef", "SwB", "1.0"),
    )
    assert await session._open() is socket
    assert "fixture-vm-bearer" not in caplog.text
