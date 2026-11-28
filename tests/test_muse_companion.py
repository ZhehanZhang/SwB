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

"""Fake phone record layer adapted from the Muse Gadget SDK's pairing tests."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import sys
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from switchboard.companion.caps import muse
from switchboard.companion.caps.base import CapabilityError
from switchboard.vendor.musegadget import generate_mac
from switchboard.vendor.musegadget.ble_framing import ChunkAssembler, encode_chunks
from switchboard.vendor.musegadget.identity import Identity
from switchboard.vendor.musegadget.pairing import PairingSession

_MAC = "02:00:00:00:00:01"
_NODE_ID = "homelink-000001"
_BLE_NAME = "MuseGadget000001"
_SDK_TOKEN = "mgst_" + "z" * 42 + "A"
_SESSION_ID = "z0eNLGw5mczvD4a1F2ubSQ"
_PROVISION = {
    "action": "provision_v2",
    "ssid": "Test network",
    "password": "test-network-password",
    "access_token": "test-device-access",
    "refresh_token": "test-device-refresh",
    "token_type": "device",
    "username": "test-account",
    "api_url": "https://hatch-api.meta.ai",
    "api_url_v2": "https://api.muse.ai",
    "noise_host": "hatch.metaaivm.com",
}
_ARGS = {"sdk_token": _SDK_TOKEN, "identity": {"mac": _MAC}, "window_s": 10}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class _FakeGatt:
    def __init__(self, name, loop, adapter):
        assert name == _BLE_NAME
        assert adapter == "hci0"
        self.scope = None
        self.characteristics = {}
        self.bus = SimpleNamespace(disconnect=self.stop)
        self.messages = asyncio.Queue()
        self.assembler = ChunkAssembler()
        self.stopped = False

    async def add_new_service(self, service_uuid):
        self.scope = service_uuid

    async def add_new_characteristic(self, service_uuid, uuid, properties, value, permissions):
        assert service_uuid == self.scope == muse.SERVICE_UUID
        self.characteristics[uuid] = SimpleNamespace(
            uuid=uuid, value=value, properties=properties, permissions=permissions,
        )

    def get_characteristic(self, uuid):
        return self.characteristics[uuid]

    def update_value(self, service_uuid, characteristic):
        assert service_uuid == muse.SERVICE_UUID and characteristic == muse.TX_UUID
        packet = self.characteristics[characteristic].value
        assert len(packet) <= 20
        if (message := self.assembler.feed(packet)) is not None:
            self.messages.put_nowait(message)
        return True

    def stop(self):
        self.stopped = True


class _Mobile:
    def __init__(self, gatt: _FakeGatt):
        self.gatt = gatt
        self.tx = AESGCM(bytes.fromhex("ee25e7f1eb3c05cc8465634e5f634deed858862b1c4c35fa434dc926f7facaeb"))
        self.rx = AESGCM(bytes.fromhex("9abb2954b05e2f5f7112e9bfd41fd970602e27ed07453c1cff51f575de5d0b4e"))
        self.tx_counter = 0
        self.rx_counter = 0

    def send(self, command):
        rx = self.gatt.get_characteristic(muse.RX_UUID)
        for packet in encode_chunks(json.dumps(command).encode()):
            self.gatt.write_request_func(rx, packet)

    async def response(self) -> dict | str:
        data = await asyncio.wait_for(self.gatt.messages.get(), timeout=2)
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return data.decode()

    def seal(self, command):
        counter = self.tx_counter
        self.tx_counter += 1
        sealed = self.tx.encrypt(
            b"\0\0\0\0" + counter.to_bytes(8, "big"), json.dumps(command).encode(),
            f"hatch-link ble setup v1|{_SESSION_ID}|m2d|{counter}".encode(),
        )
        self.send({
            "action": "pairing_encrypted", "session_id": _SESSION_ID,
            "counter": str(counter), "ciphertext": _b64(sealed[:-16]), "tag": _b64(sealed[-16:]),
        })

    async def opened(self) -> dict:
        envelope = await self.response()
        assert envelope["type"] == "pairing_encrypted"
        assert envelope["session_id"] == _SESSION_ID
        counter = self.rx_counter
        self.rx_counter += 1
        assert envelope["counter"] == str(counter)
        plain = self.rx.decrypt(
            b"\x01\0\0\0" + counter.to_bytes(8, "big"),
            _unb64(envelope["ciphertext"]) + _unb64(envelope["tag"]),
            f"hatch-link ble setup v1|{_SESSION_ID}|d2m|{counter}".encode(),
        )
        return json.loads(plain)

    async def pair(self) -> None:
        self.send({"action": "get_device_info"})
        assert await self.response() == {
            "type": "device_info", "node_id": _NODE_ID, "version": "1.0.0",
            "device_id": "hatch-link:" + _MAC, "mac": _MAC, "model": "hatch_link",
            "pairing_protocol": 5, "pairing_auth": "none", "pairing_auth_epoch": 0,
            "pairing_policy": "confirm_app", "build_sha": "", "network_ready": True,
        }
        self.send({
            "action": "pairing_client_hello", "version": 5, "pairing_auth": "none",
            "pairing_policy": "confirm_app", "mobile_nonce": "AAECAwQFBgcICQoLDA0ODw",
            "mobile_pub": "BGsX0fLhLEJH-Lzm5WOkQPJ3A32BLeszoPShOUXYmMKWT-NC4v4af5uO5-tKfA-eFivOM1drMV7Oy7ZAaDe_UfU",
        })
        ready = await self.response()
        assert ready["type"] == "pairing_ready"
        assert ready["session_id"] == _SESSION_ID
        self.seal({"action": "pairing_client_finished"})
        assert await self.opened() == {
            "type": "status", "status": "pairing_confirmed", "sdk_token": _SDK_TOKEN,
        }


class _Context:
    job_id = "job-test"

    def __init__(self):
        self.open = asyncio.Event()
        self.claimed = asyncio.Event()
        self.release = asyncio.Future()
        self.claims = []

    async def progress(self, data):
        assert data == {"ble_name": _BLE_NAME, "window_s": 10}
        self.open.set()

    async def claim(self, kind, payload):
        self.claims.append((kind, payload))
        self.claimed.set()
        return await self.release


@pytest.fixture
def fake_ble(monkeypatch):
    servers = []

    def create(**kwargs):
        servers.append(_FakeGatt(**kwargs))
        return servers[-1]

    def session(**kwargs):
        return PairingSession(
            **kwargs, generate_key=lambda: ec.derive_private_key(2, ec.SECP256R1()),
            random_bytes=lambda n: _unb64("EBESExQVFhcYGRobHB0eHw"),
        )

    async def start_bluez(self, unsubscribe):
        server = self._server
        assert server.characteristics[muse.RX_UUID].properties == 3
        assert server.characteristics[muse.TX_UUID].properties == 12
        server.unsubscribe = unsubscribe

    fake = SimpleNamespace(
        BlessServer=create,
        GATTCharacteristicProperties=SimpleNamespace(write=1, write_without_response=2, read=4, notify=8),
        GATTAttributePermissions=SimpleNamespace(writeable=1, readable=2),
    )
    monkeypatch.setattr(muse, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(muse, "_load_bless", lambda: fake)
    monkeypatch.setattr(muse, "_linux_adapter", lambda: "hci0")
    monkeypatch.setattr(muse, "__version__", "1.0.0")
    monkeypatch.setattr(muse, "PairingSession", session)
    monkeypatch.setattr(muse, "CHUNK_STAGGER_S", 0)
    monkeypatch.setattr(muse, "BLE_SHUTDOWN_DELAY_S", 0)
    monkeypatch.setattr(muse._BlessTransport, "_start_bluez", start_bluez)
    return servers


@pytest.mark.parametrize("accepted", [True, False, None])
async def test_ble_pairing_claim_is_confirmed_before_auth_ok(fake_ble, caplog, accepted):
    caplog.set_level(logging.DEBUG)
    ctx = _Context()
    cap = muse.MuseCapability()
    task = asyncio.create_task(cap.invoke("pair", _ARGS, ctx))
    try:
        await asyncio.wait_for(ctx.open.wait(), timeout=2)
        phone = _Mobile(fake_ble[0])
        phone.send(_PROVISION)
        assert await phone.response() == "error_encryption_required"
        await phone.pair()
        phone.seal({"action": "wifi_scan"})
        assert await phone.opened() == {
            "type": "wifi_scan_result",
            "networks": [{"ssid": "Use current connection", "rssi": -40, "secure": False}],
        }
        phone.seal({**_PROVISION, "api_url_v2": "https://api.muse.ai.invalid"})
        assert (await phone.opened())["status"] == "error_invalid_host"
        assert not ctx.claims
        phone.seal(_PROVISION)
        assert (await phone.opened())["status"] == "wifi_connecting"
        assert (await phone.opened())["status"] == "wifi_connected"
        await asyncio.wait_for(ctx.claimed.wait(), timeout=2)
        assert ctx.claims == [("muse_pairing", {
            "credentials": {
                field: _PROVISION[field] for field in
                ("access_token", "refresh_token", "username", "api_url", "api_url_v2", "noise_host")
            },
            "node_id": _NODE_ID,
        })]
        assert not task.done()
        assert phone.gatt.messages.empty()
        if accepted is None:
            ctx.release.set_exception(RuntimeError(_PROVISION["access_token"]))
        else:
            ctx.release.set_result({"ok": accepted})
        if accepted:
            assert (await phone.opened())["status"] == "auth_ok"
            assert await task == {"paired": True, "node_id": _NODE_ID}
        else:
            assert (await phone.opened())["status"] == "auth_failed"
            with pytest.raises(CapabilityError, match="Deployment refused the pairing"):
                await task
        assert phone.gatt.stopped
        for secret in (_SDK_TOKEN, _PROVISION["password"], _PROVISION["access_token"], _PROVISION["refresh_token"]):
            assert secret not in caplog.text
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_ble_pairing_cancel_releases_adapter_without_claiming(fake_ble):
    ctx = _Context()
    capability = muse.MuseCapability()
    task = asyncio.create_task(capability.invoke("pair", _ARGS, ctx))
    await asyncio.wait_for(ctx.open.wait(), timeout=2)
    with pytest.raises(CapabilityError, match="already open"):
        await capability.invoke("pair", _ARGS, ctx)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake_ble[0].stopped
    assert not ctx.claims


async def test_ble_pairing_timeout_releases_adapter(fake_ble):
    class Context(_Context):
        async def progress(self, data):
            assert data == {"ble_name": _BLE_NAME, "window_s": 1}

    ctx = Context()
    with pytest.raises(CapabilityError, match="Pairing window closed"):
        await muse.MuseCapability().invoke("pair", {**_ARGS, "window_s": 1}, ctx)
    assert fake_ble[0].stopped
    assert not ctx.claims


def test_muse_identity_and_availability(monkeypatch):
    assert generate_mac(lambda n: bytes.fromhex("ff0000000001")) == "fe:00:00:00:00:01"
    assert Identity(_MAC).ble_name == _BLE_NAME
    assert Identity(_MAC).for_pairing({"node_id": "gadget-000001"}).node_id == "gadget-000001"
    monkeypatch.setattr(muse, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setitem(sys.modules, "bless", None)
    cap = muse.MuseCapability()
    assert cap.available() == "Install swb-one[companion]"
    monkeypatch.setattr(muse, "_load_bless", lambda: object())
    monkeypatch.setattr(muse, "_linux_adapter", lambda: None)
    assert cap.available() == "Bluetooth adapter unavailable"


async def test_corebluetooth_constructor_has_event_loop():
    server = object()
    loop = asyncio.get_running_loop()

    def create(**params):
        assert asyncio.get_event_loop() is params["loop"] is loop
        return server

    transport = muse._BlessTransport(SimpleNamespace(BlessServer=create), _BLE_NAME)
    assert await transport._create_corebluetooth({"loop": loop}) is server


async def test_corebluetooth_keeps_muse_name_in_advertisement_and_service_in_gatt(fake_ble, monkeypatch):
    monkeypatch.setattr(muse, "sys", SimpleNamespace(platform="darwin"))
    server = _FakeGatt(name=_BLE_NAME, loop=asyncio.get_running_loop(), adapter="hci0")
    transport = muse._BlessTransport(muse._load_bless(), _BLE_NAME)

    async def create(_params):
        return server

    async def start(*, prioritize_local_name):
        assert server.scope == muse.SERVICE_UUID
        assert set(server.characteristics) == {muse.RX_UUID, muse.TX_UUID}
        name_size = 2 + len(_BLE_NAME.encode("ascii"))
        assert 3 + name_size + 18 > 31
        assert 3 + name_size <= 31
        assert prioritize_local_name is True
        return True

    monkeypatch.setattr(transport, "_create_corebluetooth", create)
    monkeypatch.setattr(server, "start", start, raising=False)
    await transport.start(None)


@pytest.mark.skipif(sys.platform != "linux", reason="BlueZ advertisement")
async def test_bluez_advertisement_has_sdk_fields_without_changing_host_name():
    pytest.importorskip("bless")
    exports = {}
    registered = []

    async def register(target):
        registered.append(target)

    async def advertise(path, options):
        registered.append(path)

    app = SimpleNamespace(
        path="/test", base_path="/test", app_name="Do not use", StopNotify=None, register=register,
    )
    adapter = SimpleNamespace(
        get_interface=lambda name: SimpleNamespace(call_register_advertisement=advertise),
    )
    setup = asyncio.Future()
    setup.set_result(None)
    transport = muse._BlessTransport(None, _BLE_NAME)
    transport._server = SimpleNamespace(
        app=app, adapter=adapter, setup_task=setup,
        bus=SimpleNamespace(export=exports.__setitem__),
    )
    await transport._start_bluez(lambda: None)
    advertisement = exports[registered[1]]
    assert registered[0] is adapter
    assert advertisement.LocalName == _BLE_NAME
    assert advertisement.ServiceUUIDs == [muse.SERVICE_UUID]
    assert advertisement.ManufacturerData[0xFFFF].value == b"\x00"
