from __future__ import annotations

import asyncio
import contextlib
import json
import re
import subprocess
import sys
import threading
from dataclasses import asdict
from pathlib import Path

from ...vendor.musegadget import __version__
from ...vendor.musegadget.ble_framing import (
    CHUNK_STAGGER_S,
    DEFAULT_ATT_MTU,
    MAX_MESSAGE_BYTES,
)
from ...vendor.musegadget.ble_setup import Credentials, ProvisionFailed, SetupController
from ...vendor.musegadget.identity import Identity
from ...vendor.musegadget.pairing import PairingSession
from .. import MUSE_PAIRING_CLAIM
from .base import ActionSpec, Capability, CapabilityError, JobContext

SERVICE_UUID = "7fdd3d1c-38ea-46cf-8b46-314ecf5f240c"
RX_UUID = "4d593029-28a2-4a6e-a1f0-3c2d5e8f9b01"
TX_UUID = "d75dc4ca-7b2b-4e9c-8f0a-1d2e3f4a5b6c"
MANUFACTURER_DATA = {0xFFFF: b"\x00"}
SETUP_WINDOW_S = 600
BLE_SHUTDOWN_DELAY_S = 1.5
_SDK_TOKEN = re.compile(r"mgst_[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]")
_NO_ADAPTER = "Bluetooth adapter unavailable"
_INSTALL_BLESS = "Install swb-one[companion]"


def _load_bless():
    try:
        import bless
    except (ImportError, OSError):
        raise CapabilityError(_INSTALL_BLESS) from None
    return bless


def _linux_adapter() -> str | None:
    return next((path.name for path in sorted(Path("/sys/class/bluetooth").glob("hci[0-9]*"))), None)


def _mac_adapter_available() -> bool:
    try:
        info = subprocess.run(
            ["/usr/sbin/system_profiler", "SPBluetoothDataType", "-json"],
            capture_output=True, check=True, timeout=3,
        )
        entries = json.loads(info.stdout)["SPBluetoothDataType"]
        return any(
            entry.get("controller_properties", {}).get("controller_state") == "attrib_on"
            for entry in entries
        )
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return False


class _CurrentConnection:
    """The pairing job arrived over the companion's authenticated connection."""

    @staticmethod
    def is_online() -> bool:
        return True

    @staticmethod
    def current_connection_entry() -> dict:
        return {"ssid": "Use current connection", "rssi": -40, "secure": False}


class _BlessTransport:
    def __init__(self, bless, name: str) -> None:
        self._bless = bless
        self._name = name
        self._loop = asyncio.get_running_loop()
        self._server = None
        self._controller = None
        self._tx = None
        self._closed = False
        self._connection = 0

    async def start(self, controller: SetupController) -> None:
        self._controller = controller
        params = {"name": self._name, "loop": self._loop}
        if sys.platform == "linux":
            params["adapter"] = _linux_adapter()
            self._server = self._bless.BlessServer(**params)
        else:
            self._server = await self._create_corebluetooth(params)
        server = self._server

        def receive(packet: bytes) -> None:
            if not self._closed:
                controller.on_write(packet)

        def write(characteristic, value, *unused) -> None:
            if (not self._closed and characteristic is not None
                    and str(characteristic.uuid).lower() == RX_UUID
                    and len(value) <= MAX_MESSAGE_BYTES):
                self._loop.call_soon_threadsafe(receive, bytes(value))

        def unsubscribe(*unused) -> None:
            self._connection += 1
            controller.on_disconnect()

        server.write_request_func = write
        server.read_request_func = lambda *_: b""
        properties = self._bless.GATTCharacteristicProperties
        permissions = self._bless.GATTAttributePermissions
        await server.add_new_service(SERVICE_UUID)
        await server.add_new_characteristic(
            SERVICE_UUID, RX_UUID, properties.write | properties.write_without_response,
            None, permissions.writeable,
        )
        await server.add_new_characteristic(
            SERVICE_UUID, TX_UUID, properties.read | properties.notify,
            None, permissions.readable,
        )
        self._tx = server.get_characteristic(TX_UUID)
        if sys.platform == "linux":
            await self._start_bluez(unsubscribe)
        elif await server.start(prioritize_local_name=False) is False:
            raise CapabilityError(_NO_ADAPTER)

    async def _create_corebluetooth(self, params: dict):
        """Bless can wait synchronously for the Bluetooth power-on callback."""
        ready = self._loop.create_future()

        def finish(server) -> None:
            if not ready.done():
                ready.set_result(server)

        def create() -> None:
            asyncio.set_event_loop(self._loop)
            try:
                server = self._bless.BlessServer(**params)
            except Exception:
                server = None
            finally:
                asyncio.set_event_loop(None)
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(finish, server)

        threading.Thread(target=create, name="swb-muse-bluetooth", daemon=True).start()
        server = await asyncio.wait_for(ready, timeout=10)
        if server is None:
            raise CapabilityError(_NO_ADAPTER)
        return server

    async def _start_bluez(self, unsubscribe) -> None:
        """Register the SDK advertisement without changing the adapter's name."""
        from bless.backends.bluezdbus.dbus.advertisement import BlueZLEAdvertisement, Type
        from dbus_next import Variant

        server = self._server
        await server.setup_task
        app = server.app
        if hasattr(app, "StopNotify"):
            app.StopNotify = unsubscribe
        advertisement = BlueZLEAdvertisement(Type.PERIPHERAL, 1, app)
        advertisement.LocalName = self._name
        advertisement.ServiceUUIDs = [SERVICE_UUID]
        advertisement.ManufacturerData = {
            company: Variant("ay", data) for company, data in MANUFACTURER_DATA.items()
        }
        server.bus.export(app.path, app)
        await app.register(server.adapter)
        server.bus.export(advertisement.path, advertisement)
        manager = server.adapter.get_interface("org.bluez.LEAdvertisingManager1")
        await manager.call_register_advertisement(advertisement.path, {})

    def mtu(self) -> int:
        return DEFAULT_ATT_MTU

    async def send_packets(self, packets: list[bytes]) -> None:
        if self._closed:
            return
        connection = self._connection
        for packet in packets:
            if connection != self._connection:
                return
            self._tx.value = bytearray(packet)
            if not self._server.update_value(SERVICE_UUID, TX_UUID):
                raise CapabilityError("Bluetooth notification failed")
            await asyncio.sleep(CHUNK_STAGGER_S)

    async def disconnect(self, delay: float) -> None:
        """Discard the session because Bless cannot disconnect centrals portably."""
        await asyncio.sleep(delay)
        self._connection += 1
        self._controller.on_disconnect()

    async def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._controller = None
        server = self._server
        if server is None:
            return
        if sys.platform == "linux":
            bus = getattr(server, "bus", None)
            if bus is not None:
                with contextlib.suppress(Exception):
                    bus.disconnect()
        else:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server.stop(), timeout=3)
                delegate = getattr(server, "peripheral_manager_delegate", None)
                if delegate is not None:
                    delegate.peripheral_manager.removeAllServices()


class MuseCapability(Capability):
    id = "muse"
    version = "1"
    title = "Muse"
    actions = {
        "pair": ActionSpec("Pair Muse", {
            "type": "object",
            "properties": {
                "sdk_token": {"type": "string"},
                "identity": {
                    "type": "object",
                    "properties": {"mac": {"type": "string", "pattern": r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$"}},
                    "required": ["mac"],
                    "additionalProperties": False,
                },
                "window_s": {"type": "integer", "minimum": 1, "maximum": SETUP_WINDOW_S},
            },
            "required": ["sdk_token", "identity"],
            "additionalProperties": False,
        }),
    }

    def __init__(self) -> None:
        self._busy = False

    def available(self) -> str | None:
        if sys.platform not in {"linux", "darwin"}:
            return "Muse Bluetooth advertising is unavailable on this platform"
        try:
            _load_bless()
        except CapabilityError as exc:
            return str(exc)
        if sys.platform == "linux" and _linux_adapter() is None:
            return _NO_ADAPTER
        if sys.platform == "darwin" and not _mac_adapter_available():
            return _NO_ADAPTER
        return None

    async def invoke(self, action: str, args: dict, ctx: JobContext) -> dict:
        if action != "pair":
            raise CapabilityError("Unknown Muse action")
        token = args.get("sdk_token")
        if not isinstance(token, str) or not _SDK_TOKEN.fullmatch(token):
            raise CapabilityError("Invalid Muse SDK token")
        identity_arg = args.get("identity")
        if not isinstance(identity_arg, dict):
            raise CapabilityError("Invalid Muse identity")
        try:
            identity = Identity(identity_arg.get("mac"))
        except ValueError:
            raise CapabilityError("Invalid Muse identity") from None
        window_s = args.get("window_s", SETUP_WINDOW_S)
        if type(window_s) is not int or not 1 <= window_s <= SETUP_WINDOW_S:
            raise CapabilityError("Invalid Muse pairing window")
        if self._busy:
            raise CapabilityError("Muse pairing is already open")
        if reason := self.available():
            raise CapabilityError(reason)

        async def provision(credentials: Credentials, commit) -> None:
            try:
                result = await ctx.claim(MUSE_PAIRING_CLAIM, {
                    "credentials": asdict(credentials),
                    "node_id": identity.setup_node_id,
                })
            except Exception:
                raise ProvisionFailed("auth_failed") from None
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise ProvisionFailed("auth_failed")
            if not commit(lambda: True):
                raise ProvisionFailed("error_storage")

        transport = _BlessTransport(_load_bless(), identity.ble_name)
        controller = SetupController(
            pairing=PairingSession(
                node_id=identity.setup_node_id, device_id=identity.device_id, mac=identity.mac,
                firmware_version=__version__, sdk_token=token,
            ),
            identity=identity,
            version=__version__,
            transport=transport,
            network=_CurrentConnection(),
            provision=provision,
        )
        self._busy = True
        try:
            async with asyncio.timeout(window_s):
                try:
                    await transport.start(controller)
                except Exception:
                    raise CapabilityError(_NO_ADAPTER) from None
                await ctx.progress({"ble_name": identity.ble_name, "window_s": window_s})
                await controller.run()
            await asyncio.sleep(BLE_SHUTDOWN_DELAY_S)
            return {"paired": True, "node_id": identity.setup_node_id}
        except ProvisionFailed:
            raise CapabilityError("Deployment refused the pairing") from None
        except TimeoutError:
            raise CapabilityError("Pairing window closed") from None
        except CapabilityError:
            raise
        except Exception:
            raise CapabilityError("Muse pairing failed") from None
        finally:
            controller.close()
            try:
                await transport.stop()
            finally:
                self._busy = False
