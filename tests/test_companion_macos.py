from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import plistlib
import stat
import sys
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from switchboard.companion import bluetooth, macos_app, macos_transport
from switchboard.companion.caps.base import CapabilityError
from switchboard.companion.state import CompanionError
from switchboard.vendor.musegadget.ble_framing import ChunkAssembler
from switchboard.vendor.musegadget.ble_setup import SetupController
from switchboard.vendor.musegadget.identity import Identity
from switchboard.vendor.musegadget.pairing import PairingSession


def app_frame(key, **data):
    raw = json.dumps(data, separators=(",", ":")).encode()
    return hmac.new(key, b"app:" + raw, hashlib.sha256).hexdigest().encode() + b" " + raw + b"\n"


async def test_channel_authenticates_direction_and_sequence():
    key = b"A" * 32
    reader = asyncio.StreamReader(limit=8192)
    writer = Mock(drain=AsyncMock())
    channel = macos_transport.Channel(reader, writer, key)
    await channel.send("status")
    data = b'{"seq":1,"type":"status"}'
    signature = hmac.new(key, b"py:" + data, hashlib.sha256).hexdigest().encode()
    assert writer.write.call_args.args == (signature + b" " + data + b"\n",)

    ready = app_frame(key, seq=1, type="ready")
    reader.feed_data(ready + app_frame(key, seq=2, type="status", permission="Allowed") + ready)
    assert (await channel.receive())["type"] == "ready"
    assert (await channel.receive())["permission"] == "Allowed"
    with pytest.raises(CapabilityError, match="authentication"):
        await channel.receive()


@pytest.mark.parametrize("frame", [b" " * 9000 + b"\n", b"0" * 64 + b' {"seq":1,"type":"ready"}\n'])
async def test_channel_rejects_oversized_or_unsigned_frames(frame):
    reader = asyncio.StreamReader(limit=8192)
    reader.feed_data(frame)
    channel = macos_transport.Channel(reader, Mock(), b"A" * 32)
    with pytest.raises(CapabilityError, match="authentication"):
        await channel.receive()


async def test_mac_negotiated_framing_precedes_the_first_write_and_resets(monkeypatch):
    key = b"A" * 32
    reader = asyncio.StreamReader(limit=8192)
    writer = Mock(drain=AsyncMock())
    transport = macos_transport.MacTransport("MuseGadget000001")
    transport.channel = macos_transport.Channel(reader, writer, key)
    identity = Identity("02:00:00:00:00:01")
    controller = SetupController(
        pairing=PairingSession(node_id=identity.setup_node_id, device_id=identity.device_id,
                               mac=identity.mac, firmware_version="1.0.0", sdk_token="mgst_" + "z" * 42 + "A"),
        identity=identity, version="1.0.0", transport=transport, network=Mock(is_online=lambda: True),
        provision=AsyncMock(),
    )
    transport.controller = Mock()
    transport.controller.on_write.side_effect = lambda _: assert_mtu(163)
    transport.controller.on_disconnect.side_effect = lambda: assert_mtu(23)
    monkeypatch.setattr(macos_transport, "CHUNK_STAGGER_S", 0)

    def assert_mtu(expected):
        assert transport.mtu() == expected

    def notifications():
        return [base64.b64decode(json.loads(call.args[0][65:])["packet"]) for call in writer.write.call_args_list]

    request = b'{"action":"get_device_info"}'
    await controller.handle_message(request)
    legacy = notifications()
    assert max(map(len, legacy)) == 20 and len(legacy) >= 16
    writer.write.reset_mock()

    reader.feed_data(app_frame(key, seq=1, type="mtu", mtu="163"))
    reader.feed_data(app_frame(key, seq=2, type="packet", packet=base64.b64encode(request).decode()))
    task = asyncio.create_task(transport._events())
    try:
        await asyncio.sleep(0)
        transport.controller.on_write.assert_called_once_with(request)
        await controller.handle_message(request)
        negotiated = notifications()
        assert negotiated == [
            b'\xfe\x00\x02{"type":"device_info","node_id":"homelink-000001","version":"1.0.0",'
            b'"device_id":"hatch-link:02:00:00:00:00:01","mac":"02:00:00:00:00:01","model":"hatch_link"',
            b'\xfe\x01\x02,"pairing_protocol":5,"pairing_auth":"none","pairing_auth_epoch":0,'
            b'"pairing_policy":"confirm_app","build_sha":"","network_ready":true}',
        ]
        for packets in (legacy, negotiated):
            assembler = ChunkAssembler()
            for packet in packets[:-1]:
                assert assembler.feed(packet) is None
            assert json.loads(assembler.feed(packets[-1])) == controller.device_info()

        reader.feed_data(app_frame(key, seq=3, type="disconnect"))
        await asyncio.sleep(0)
        transport.controller.on_disconnect.assert_called_once_with()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        controller.close()


@pytest.mark.parametrize("value", [None, 163, "22", "164", "0163", "+23", "2٣", "23\n", "9" * 100])
async def test_mac_rejects_invalid_authenticated_notification_size(value):
    key = b"A" * 32
    reader = asyncio.StreamReader(limit=8192)
    reader.feed_data(app_frame(key, seq=1, type="mtu", mtu=value))
    transport = macos_transport.MacTransport("MuseGadget000001")
    transport.channel = macos_transport.Channel(reader, Mock(), key)
    with pytest.raises(CapabilityError, match="invalid notification size"):
        await transport._events()
    assert transport.mtu() == 23


@pytest.mark.skipif(os.name != "posix", reason="needs a Unix socket")
async def test_helper_context_closes_connected_peer_before_waiting_for_server(monkeypatch):
    spawn = asyncio.create_subprocess_exec
    child = """
import hashlib, hmac, json, socket, sys
from pathlib import Path
file = Path(sys.argv[1])
data = json.loads(file.read_bytes())
file.unlink()
key = bytes.fromhex(data["token"])
body = b'{"seq":1,"type":"ready"}'
frame = hmac.new(key, b"app:" + body, hashlib.sha256).hexdigest().encode() + b" " + body + b"\\n"
with socket.socket(socket.AF_UNIX) as conn:
    conn.connect(data["socket"])
    conn.sendall(frame)
    assert conn.recv(1) == b""
"""

    async def direct(*args, **kwargs):
        return await spawn(sys.executable, "-c", child, args[-1], **kwargs)

    with tempfile.TemporaryDirectory(prefix="swb-ipc-", dir="/tmp") as directory:
        home = Path(directory)
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", direct)
        monkeypatch.setattr(macos_transport, "_same_user", lambda _: True)
        async with asyncio.timeout(7):
            async with macos_transport.helper(home):
                pass
        assert not list(home.rglob("bootstrap")) and not list(home.rglob("s"))


@pytest.mark.skipif(sys.platform != "darwin" or not os.environ.get("SWB_TEST_MAC_HELPER"), reason="needs the built Mac app")
async def test_native_helper_authenticates_commands_without_requesting_bluetooth(monkeypatch):
    app = Path(os.environ["SWB_TEST_MAC_HELPER"])
    executable = app / "Contents" / "MacOS" / "SwBCompanion"
    spawn = asyncio.create_subprocess_exec

    async def direct(*args, **kwargs):
        bootstrap = Path(args[-1])
        data = json.loads(bootstrap.read_bytes())
        assert bootstrap.stat().st_mode & 0o777 == 0o600
        assert bootstrap.parent.stat().st_mode & 0o777 == 0o700
        assert Path(data["socket"]).stat().st_mode & 0o777 == 0o600
        return await spawn(str(executable), str(bootstrap), **kwargs)

    with tempfile.TemporaryDirectory(prefix="swb-ble-", dir="/tmp") as directory:
        home = Path(directory)
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", direct)
        async with macos_transport.helper(app) as channel:
            await channel.send("status")
            response = await asyncio.wait_for(channel.receive(), 5)
            assert response["type"] == "status"
            assert response["permission"] in ("Approval required", "Allowed", "Denied", "Restricted", "Unavailable")
            assert not list(home.rglob("bootstrap"))
            channel.sent = 0
            await channel.send("status")
            with pytest.raises(CapabilityError, match="authentication"):
                await asyncio.wait_for(channel.receive(), 5)
        assert not list(home.rglob("s"))


def test_download_uses_pinned_version_and_hash(tmp_path, monkeypatch):
    data = b"archive"
    opener = Mock()
    opener.open.side_effect = lambda *_args, **_kwargs: BytesIO(data)
    monkeypatch.setattr(macos_app.urllib.request, "build_opener", lambda *_: opener)
    archive = tmp_path / "archive.zip"
    macos_app._download(archive, hashlib.sha256(data).hexdigest())
    assert archive.read_bytes() == data
    opener.open.assert_called_once_with(
        f"https://github.com/ZhehanZhang/SwB/releases/download/v{macos_app.__version__}/swb-companion-macos.zip",
        timeout=30,
    )
    with pytest.raises(CompanionError, match="did not match"):
        macos_app._download(tmp_path / "wrong.zip", "0" * 64)


@pytest.mark.parametrize("name", ["../bad", "/bad", "SwB Companion.app/../../bad", "SwB Companion.app/good\\bad"])
def test_download_rejects_unsafe_archive_paths(tmp_path, monkeypatch, name):
    archive = tmp_path / "test.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr(name, b"bad")
    monkeypatch.setattr(macos_app, "_system", Mock(side_effect=AssertionError))
    with pytest.raises(CompanionError, match="invalid layout"):
        macos_app._extract(archive, tmp_path)


def test_download_rejects_symlinks(tmp_path, monkeypatch):
    archive = tmp_path / "test.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        entry = zipfile.ZipInfo("SwB Companion.app/Contents/link")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        stream.writestr(entry, b"/tmp")
    monkeypatch.setattr(macos_app, "_system", Mock(side_effect=AssertionError))
    with pytest.raises(CompanionError, match="invalid layout"):
        macos_app._extract(archive, tmp_path)


def test_verify_pins_developer_id_and_uses_both_macos_checks(tmp_path, monkeypatch):
    app = tmp_path / macos_app.APP_NAME
    info = app / "Contents" / "Info.plist"
    info.parent.mkdir(parents=True)
    info.write_bytes(plistlib.dumps({
        "CFBundleIdentifier": macos_app.BUNDLE_ID,
        "CFBundleExecutable": "SwBCompanion",
        "CFBundleShortVersionString": macos_app.__version__,
    }))
    system = Mock()
    monkeypatch.setattr(macos_app, "_system", system)
    macos_app.verify_app(app, "ABCDEFGHIJ", gatekeeper=True)
    verify, assess = [call.args for call in system.call_args_list]
    assert verify[:5] == ("/usr/bin/codesign", "--verify", "--deep", "--strict", "-R")
    assert verify[5].startswith("=anchor apple generic")
    assert 'certificate leaf[subject.OU] = "ABCDEFGHIJ"' in verify[5]
    assert macos_app.BUNDLE_ID in verify[5]
    assert assess[:4] == ("/usr/sbin/spctl", "--assess", "--type", "execute")


@pytest.mark.parametrize("result", ["Allowed", "Denied", "Approval required"])
def test_bluetooth_prepare_only_prompts_when_needed(tmp_path, monkeypatch, capsys, result):
    path = tmp_path / "companion.json"
    install = Mock()
    permission = AsyncMock(side_effect=[result, "Allowed"])
    monkeypatch.setattr(macos_app, "install_app", install)
    monkeypatch.setattr(macos_transport, "permission", permission)
    if result == "Denied":
        with pytest.raises(CompanionError, match="SwB Companion"):
            bluetooth.prepare(path)
    else:
        bluetooth.prepare(path)
    install.assert_called_once_with()
    assert permission.await_count == (2 if result == "Approval required" else 1)
    if result == "Approval required":
        permission.assert_awaited_with(authorize=True)
        assert "SwB Companion" in capsys.readouterr().out
    assert bluetooth.last_check(path) == f"{'Denied' if result == 'Denied' else 'Allowed'} (last check)"
