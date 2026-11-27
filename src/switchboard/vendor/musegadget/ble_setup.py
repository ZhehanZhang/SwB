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

"""BLE setup commands: the protocol logic behind the GATT characteristics.

Independent of BlueZ: a transport delivers writes and sends framed
notifications. Messages are handled in arrival order on the event loop so
encrypted record counters reach the phone in order.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

from . import server_hosts
from .ble_framing import ChunkAssembler, encode_chunks
from .identity import Identity
from .pairing import PairingError, PairingSession

SENSITIVE_ACTIONS = frozenset({
    "provision", "provision_v2", "wifi_scan", "ota", "device.ota",
    "unpair", "set_wifi", "set_auth",
})
PLAINTEXT_STATUSES = frozenset({
    "error_encryption_required",
    "error_pairing_invalid_hello",
    "error_pairing_unavailable",
    "error_pairing_decrypt",
})
DISCONNECT_AFTER_ERROR_S = 0.3


class Transport(Protocol):
    async def send_packets(self, packets: list[bytes]) -> None:
        """Notify each packet in order."""

    def mtu(self) -> int: ...

    async def disconnect(self, delay: float) -> None: ...


class Network(Protocol):
    def is_online(self) -> bool: ...

    def current_connection_entry(self) -> dict: ...


@dataclass(frozen=True)
class Credentials:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    username: str = field(repr=False)
    api_url: str
    api_url_v2: str
    noise_host: str


class ProvisionFailed(Exception):
    """Setup could not finish; ``status`` is what the app is told."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


def _compact(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"))


class SetupController:
    """Handles one BLE setup client at a time.

    ``provision`` hands the credentials to the deployment. It must call
    ``commit`` with the confirmed result, raising :class:`ProvisionFailed`
    on failure. The companion keeps no local record.
    """

    def __init__(
        self,
        *,
        pairing: PairingSession,
        identity: Identity,
        version: str,
        transport: Transport,
        network: Network,
        provision: Callable[[Credentials, Callable[[Callable[[], bool]], bool]], Awaitable[None]],
    ) -> None:
        self._pairing = pairing
        self._identity = identity
        self._version = version
        self._transport = transport
        self._network = network
        self._provision = provision
        self._assembler = ChunkAssembler()
        self._inbox: asyncio.Queue[bytes] = asyncio.Queue(maxsize=32)
        self._plaintext_blocked = False
        self._completed = False

    def on_write(self, packet: bytes) -> None:
        if self._completed:
            return
        message = self._assembler.feed(packet)
        if message is not None and not self._inbox.full():
            self._inbox.put_nowait(message)

    def on_disconnect(self) -> None:
        self._assembler.reset()
        while not self._inbox.empty():
            self._inbox.get_nowait()
        self._plaintext_blocked = False
        self._pairing.reset()

    def close(self) -> None:
        self.on_disconnect()
        self._pairing.close()

    async def run(self) -> None:
        while not self._completed:
            await self.handle_message(await self._inbox.get())

    async def handle_message(self, raw: bytes, decrypted: bool = False) -> None:
        try:
            command = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError):
            await self.send_status("error_invalid_command")
            return
        if not isinstance(command, dict):
            await self.send_status("error_invalid_command")
            return
        action = command.get("action")
        action = action if isinstance(action, str) else ""
        if not decrypted and action == "pairing_client_hello":
            await self._handle_hello(command)
        elif not decrypted and action == "pairing_encrypted":
            await self._handle_record(command)
        elif action == "get_device_info":
            # Public metadata, safe in plaintext at any point. Apps re-read it
            # when they restart a handshake on the same connection.
            await self.send_json(self.device_info())
        elif not decrypted and self._plaintext_blocked:
            return
        elif not decrypted and action in SENSITIVE_ACTIONS:
            await self.send_status("error_encryption_required")
        elif decrypted and action == "pairing_client_finished":
            await self._handle_client_finished(command)
        elif decrypted and action in SENSITIVE_ACTIONS and not self._pairing.confirmed:
            await self.send_status("error_pairing_confirm_required")
        elif decrypted and action == "wifi_scan":
            await self._handle_wifi_scan()
        elif decrypted and action == "provision_v2":
            await self._handle_provision(command)
        else:
            await self.send_status("error_unknown_action")

    def device_info(self) -> dict:
        info = {
            "type": "device_info",
            "node_id": self._identity.setup_node_id,
            "version": self._version,
            **self._pairing.device_info(),
            "build_sha": "",
            "network_ready": self._network.is_online(),
        }
        return info

    async def _handle_hello(self, command: dict) -> None:
        try:
            ready = self._pairing.handle_hello(command)
        except PairingError as err:
            await self.send_status(err.status)
            return
        self._plaintext_blocked = True
        await self.send_json(ready)

    async def _handle_record(self, envelope: dict) -> None:
        try:
            plaintext = self._pairing.decrypt(envelope)
        except PairingError as err:
            await self.send_status(err.status)
            await self._transport.disconnect(DISCONNECT_AFTER_ERROR_S)
            return
        await self.handle_message(plaintext.encode("utf-8"), decrypted=True)

    async def _handle_client_finished(self, command: dict) -> None:
        generation = self._pairing.handle_client_finished(command)
        if not generation:
            await self.send_status("error_pairing_decrypt")
            await self._transport.disconnect(DISCONNECT_AFTER_ERROR_S)
            return
        await self.send_status("pairing_confirmed", generation)

    async def _handle_wifi_scan(self) -> None:
        networks = [self._network.current_connection_entry()] if self._network.is_online() else []
        await self.send_encrypted_json({"type": "wifi_scan_result", "networks": networks})

    async def _handle_provision(self, command: dict) -> None:
        def text(key: str) -> str:
            value = command.get(key)
            return value if isinstance(value, str) else ""

        if (
            not isinstance(command.get("ssid"), str)
            or not isinstance(command.get("password"), str)
            or not text("access_token")
            or not text("refresh_token")
            or text("token_type") != "device"
        ):
            await self.send_status("error_missing_credentials")
            return
        # Setup may name the servers the device sends its credentials to; one
        # this client doesn't allow (server_hosts) refuses the whole command
        # before any of it is applied.
        if not (server_hosts.api_allowed(command.get("api_url"))
                and server_hosts.api_allowed(command.get("api_url_v2"))
                and server_hosts.noise_allowed(command.get("noise_host"))):
            await self.send_status("error_invalid_host")
            return
        generation = self._pairing.mark_provisioning()
        if not generation:
            await self.send_status("error_pairing_confirm_required")
            return
        # The Wi-Fi fields are deliberately dropped: this device only sets up
        # when it is already online.
        credentials = Credentials(
            access_token=text("access_token"),
            refresh_token=text("refresh_token"),
            username=text("username"),
            api_url=text("api_url"),
            api_url_v2=text("api_url_v2"),
            noise_host=text("noise_host"),
        )
        await self.send_status("wifi_connecting", generation)
        if not self._network.is_online():
            self._pairing.extend_provisioning(generation)
            await self.send_status("wifi_failed", generation)
            return
        await self.send_status("wifi_connected", generation)

        def commit(confirmed: Callable[[], bool]) -> bool:
            return self._pairing.commit_provisioning(generation, confirmed)

        if not self._pairing.extend_provisioning(generation):
            return
        try:
            await self._provision(credentials, commit)
        except ProvisionFailed as err:
            await self.send_status(err.status, generation)
            await self._transport.disconnect(0.5)
            raise
        await self.send_status("auth_ok", generation)
        self._completed = True

    async def send_status(self, status: str, generation: int = 0) -> None:
        envelope = self._pairing.encrypt_status(status, generation)
        if envelope is not None:
            await self.send_json(envelope)
        elif not generation and not self._plaintext_blocked and status in PLAINTEXT_STATUSES:
            payload = status.encode()
            mtu = self._transport.mtu()
            await self._transport.send_packets([payload] if len(payload) <= mtu - 3 else encode_chunks(payload, mtu))

    async def send_json(self, obj: dict) -> None:
        await self._transport.send_packets(encode_chunks(_compact(obj).encode(), self._transport.mtu()))

    async def send_encrypted_json(self, obj: dict, generation: int = 0) -> None:
        envelope = self._pairing.encrypt_json(_compact(obj), generation)
        if envelope is not None:
            await self.send_json(envelope)
