"""Keep an in-process session for the owner's paired Muse gadget."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from contextlib import aclosing
from typing import TYPE_CHECKING, Callable

from . import __version__
from .vendor.musegadget import muse_api, server_hosts
from .vendor.musegadget.link_client import DeviceDescription, HttpStreamError, LinkSession, Outcome, WakeNotSent
from .vendor.musegadget.muse_turn import MuseTurn

if TYPE_CHECKING:
    from .vendor.musegadget.identity import Identity

log = logging.getLogger(__name__)

TOKEN_REFRESH_AGE_S = 3 * 3600
TOKEN_RETRY_S = 300
BACKOFF_BASE_S = 2.0
BACKOFF_MAX_S = 60.0
AUTH_BACKOFF_MIN_S = 15.0
HEALTHY_SESSION_S = 30.0
HEALTHY_RECONNECT_S = 1.0
WAKE_TIMEOUT_S = 120.0
MESSAGE_LIMIT = 20_000
_SESSION_ID = re.compile(r"[a-zA-Z0-9-]{1,64}")


class WakeUnavailable(ConnectionError):
    """SwB sent no wake request. It is safe to wait for reconnect and retry."""


class WakeRejected(ConnectionError):
    """Muse explicitly refused a wake request. Do not retry it automatically."""


class WakeDeliveryUnknown(ConnectionError):
    """Muse may have received the wake. Do not retry it automatically."""


class MuseGadget:
    def __init__(self, *, identity: Identity, pairing: dict, sdk_token: str,
                 save_pairing: Callable[[dict | None], None], display_name: str = "SwB", connect=None):
        self._pairing: dict | None = dict(pairing)
        self._sdk_token = sdk_token
        self._save_pairing = save_pairing
        self._api = muse_api.api_root(pairing.get("api_url_v2", ""))
        self._host = server_hosts.noise_host(pairing.get("noise_host"))
        self._device = DeviceDescription(identity.for_pairing(pairing).node_id, display_name, __version__)
        self._connect = connect
        self._ready = asyncio.Event()
        self._session: LinkSession | None = None
        self._session_stop: asyncio.Event | None = None
        self._last_refresh_attempt = float("-inf")
        self._needs_save = False
        self._running = False

    @property
    def online(self) -> bool:
        return self._ready.is_set() and self._session is not None and self._session.online

    async def wait_ready(self) -> None:
        while not self.online:
            await self._ready.wait()
            if not self.online:
                self._ready.clear()

    async def run(self) -> None:
        """Run until cancelled or Muse removes the pairing."""
        if self._running:
            raise RuntimeError("Muse gadget is already running")
        self._running = True
        backoff = BACKOFF_BASE_S
        token_rejected = False
        try:
            while self._pairing is not None or self._needs_save:
                delay = backoff
                try:
                    self._persist_pairing()
                    await self._maybe_refresh()
                    if self._pairing is None:
                        return
                    vms, status = await asyncio.to_thread(
                        muse_api.fetch_vms_with_status, self._pairing["access_token"], self._api,
                    )
                    if status == 401:
                        refreshed = await self._maybe_refresh(force=True)
                        if self._pairing is None:
                            return
                        if refreshed and not token_rejected:
                            token_rejected = True
                            continue
                        token_rejected = True
                        delay = max(backoff, AUTH_BACKOFF_MIN_S) if refreshed else TOKEN_RETRY_S
                    else:
                        token_rejected = False
                        vm = next((entry for entry in vms if entry["is_default"]), vms[0] if vms else None)
                        if vm is not None:
                            outcome, lasted = await self._serve(vm)
                            if outcome is Outcome.UNPAIRED:
                                if self._pairing is not None:
                                    self._pairing = None
                                    self._needs_save = True
                                    self._persist_pairing()
                                return
                            if lasted >= HEALTHY_SESSION_S:
                                backoff = BACKOFF_BASE_S
                                delay = HEALTHY_RECONNECT_S
                            if outcome in (Outcome.AUTH_REJECTED, Outcome.FORBIDDEN):
                                delay = max(delay, AUTH_BACKOFF_MIN_S)
                except Exception:
                    log.warning("Muse gadget session failed")
                await asyncio.sleep(delay)
                backoff = min(backoff * 2, BACKOFF_MAX_S)
        finally:
            self._running = False

    def _refresh_delay(self) -> float:
        if self._pairing is None:
            return 0
        saved_at = self._pairing.get("access_token_saved_at", 0)
        if not isinstance(saved_at, (int, float)) or not 0 <= saved_at <= time.time():
            saved_at = 0
        return max(saved_at + TOKEN_REFRESH_AGE_S - time.time(),
                   self._last_refresh_attempt + TOKEN_RETRY_S - time.monotonic(), 0)

    async def _maybe_refresh(self, *, force: bool = False) -> bool:
        if self._pairing is None or (not force and self._refresh_delay()):
            return False
        self._last_refresh_attempt = time.monotonic()
        rotation = asyncio.create_task(self._rotate(self._pairing))
        try:
            return await asyncio.shield(rotation)
        except asyncio.CancelledError:
            await asyncio.gather(rotation, return_exceptions=True)
            raise

    async def _rotate(self, pairing: dict) -> bool:
        tokens, status = await asyncio.to_thread(
            muse_api.refresh_device_token, pairing["refresh_token"], self._device.node_id, self._api, self._sdk_token,
        )
        if tokens:
            self._pairing = {**pairing, **tokens, "access_token_saved_at": int(time.time())}
        elif status == 401:
            self._pairing = None
        else:
            return False
        self._needs_save = True
        self._persist_pairing()
        return bool(tokens)

    def _persist_pairing(self) -> None:
        if self._needs_save:
            self._save_pairing(dict(self._pairing) if self._pairing is not None else None)
            self._needs_save = False

    async def _serve(self, vm: dict) -> tuple[Outcome, float]:
        session = LinkSession(noise_host=self._host, vm_id=vm["vm_id"], vm_auth_token=vm["vm_auth_token"],
                              device=self._device, connect=self._connect)
        stop = asyncio.Event()
        self._session = session
        self._session_stop = stop
        running = asyncio.create_task(session.run(stop))
        registered = asyncio.create_task(session.registered.wait())
        try:
            await asyncio.wait({running, registered}, return_when=asyncio.FIRST_COMPLETED)
            if session.online:
                self._ready.set()
            while not running.done():
                await asyncio.wait({running}, timeout=self._refresh_delay())
                if not running.done():
                    await self._maybe_refresh()
                    if self._pairing is None:
                        return Outcome.UNPAIRED, 0
            lasted = time.monotonic() - session.registered_at if session.registered_at is not None else 0
            return running.result(), lasted
        finally:
            self._ready.clear()
            self._session = self._session_stop = None
            for task in (registered, running):
                task.cancel()
            await asyncio.gather(registered, running, return_exceptions=True)

    def _unavailable(self, session: LinkSession | None) -> WakeUnavailable:
        if session is self._session:
            self._ready.clear()
            if self._session_stop is not None:
                self._session_stop.set()
        return WakeUnavailable("Muse was unavailable before the wake was sent")

    async def wake(self, session_id: str, task: str, *, timeout: float = WAKE_TIMEOUT_S) -> str | None:
        """Send once to a fresh side chat. Return a complete reply, or None after acknowledgement."""
        if (not _SESSION_ID.fullmatch(session_id) or not task.strip() or len(task) > MESSAGE_LIMIT
                or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("Muse needs a side chat ID, a short text message, and a positive timeout")
        session = self._session
        if not self.online or session is None:
            raise self._unavailable(session)

        turn = MuseTurn(session_id)
        opened, changed = asyncio.Event(), asyncio.Event()

        async def follow() -> None:
            async with aclosing(session.subscribe_chat(session_id, opened=opened)) as events:
                async for event in events:
                    turn.feed(event)
                    changed.set()

        follower = asyncio.create_task(follow())
        ready = asyncio.create_task(opened.wait())
        sending = acknowledged = False
        try:
            async with asyncio.timeout(timeout):
                await asyncio.wait({ready, follower}, return_when=asyncio.FIRST_COMPLETED)
                fresh = False
                if follower.done():
                    error = follower.exception()
                    if isinstance(error, HttpStreamError) and error.status == 404:
                        fresh = True
                    else:
                        raise self._unavailable(session)
                sending = True
                status, response = await session.send_chat(task, session_id)
                if 400 <= status < 500:
                    if status in (401, 403) and session is self._session:
                        self._unavailable(session)
                    raise WakeRejected(f"Muse refused the wake (HTTP {status})")
                if not 200 <= status < 300 or response is None:
                    raise WakeDeliveryUnknown("Muse did not acknowledge the wake")
                turn.acknowledge(response, fresh=fresh)
                acknowledged = True
                if fresh:
                    follower = asyncio.create_task(follow())
                while True:
                    if turn.complete():
                        return turn.text or None
                    if follower.done():
                        return None
                    changed.clear()
                    try:
                        await asyncio.wait_for(changed.wait(), 0.5)
                    except TimeoutError:
                        pass
        except WakeNotSent:
            raise self._unavailable(session) from None
        except (WakeUnavailable, WakeRejected, WakeDeliveryUnknown):
            raise
        except Exception:
            if acknowledged:
                return None
            if not sending:
                raise self._unavailable(session) from None
            raise WakeDeliveryUnknown("Muse did not acknowledge the wake") from None
        finally:
            for pending in (ready, follower):
                pending.cancel()
            await asyncio.gather(ready, follower, return_exceptions=True)
