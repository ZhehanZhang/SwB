"""Pair and wake the owner's Muse gadget over the public SDK."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from shlex import quote
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from uuid import uuid4

from ..agents import PRESETS
from ..auth import Principal
from ..claims import ClaimInputError
from ..connect import public_https_prerequisite
from ..refusals import OWNER_REQUIRED, SCOPE_REQUIRED
from ..trust import frame_sender
from . import Action, Address, Block, BlockError, BlockRefused, Call, Manifest, TargetKind

if TYPE_CHECKING:
    from ..core import Switchboard
    from ..muse_gadget import MuseGadget
    from ..vendor.musegadget.identity import Identity

TARGET = "muse:gadget:muse"
TOKEN_ENV = "MUSE_SDK_TOKEN"
IDENTITY_SECRET = "MUSE_GADGET_IDENTITY"
PAIRING_SECRET = "MUSE_GADGET_PAIRING"
SDK_TOKENS = "https://gadgets.muse.ai/settings/sdk-tokens"
SDK_TOKEN = re.compile(r"mgst_[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]")
CREDENTIALS = ("access_token", "refresh_token", "username", "api_url", "api_url_v2", "noise_host")
PAIRING_WINDOW = 600
DUPLICATE_WINDOW = 600
MESSAGE_LIMIT = 20_000


def validate_sdk_token(value: str) -> None:
    if not SDK_TOKEN.fullmatch(value):
        raise ClaimInputError("Enter a Muse SDK token.")


class MuseBlock(Block):
    connect_kinds = ("muse",)
    wakes_agents = True
    manifest = Manifest(
        id="muse", title="Muse", kind="sink", level_out="L3",
        verbs={"send": "send", "status": "result", "result": "result", "cancel": "cancel"},
        agents=(PRESETS["muse"],), targets=(TargetKind("gadget", persona=True),), secrets=(TOKEN_ENV,),
        instructions="Send to muse to wake the paired gadget. Use agent:muse for the Muse OAuth mailbox.",
    )

    def __init__(self, core: Switchboard, config: dict[str, Any]):
        super().__init__(core, config)
        if self.name != "muse":
            raise BlockError("Configure the Muse gadget under blocks.muse.")
        core.claims.register_name(TOKEN_ENV, validate_sdk_token)
        self._gadget: MuseGadget | None = None
        self._tasks: tuple[asyncio.Task[Any], ...] = ()
        self._queued = asyncio.Event()
        self._pairing: tuple[str, Identity] | None = None
        self._verified_pairing = False
        self._pair_window_closed = False
        self._claim_lock = asyncio.Lock()

    def bind(self) -> None:
        if companions := getattr(self.core, "companions", None):
            companions.register_claim("muse_pairing", self._claim_pairing, capability="muse", action="pair")

    @property
    def online(self) -> bool:
        return self._gadget is not None and self._gadget.online

    def _identity(self) -> Identity:
        from ..vendor.musegadget.identity import Identity, generate_mac

        if not (mac := self.core.claims.secret(IDENTITY_SECRET)):
            self.core.claims.save_secret(IDENTITY_SECRET, generate_mac(), replace=False)
            mac = self.core.claims.secret(IDENTITY_SECRET)
        return Identity(mac)

    def _token(self) -> str | None:
        token = self.core.secret(TOKEN_ENV)
        return token if token and SDK_TOKEN.fullmatch(token) else None

    def config_step(self) -> str | None:
        return None if self._token() else 'Run connect(what="muse") for the Muse SDK token link.'

    def next_step(self) -> str | None:
        if not self._token():
            return self.config_step()
        if self._gadget is None:
            return 'Run connect(what="muse") to pair Muse on a companion.'
        return None

    def status(self) -> dict[str, Any]:
        return {"state": "online" if self.online else "offline" if self._gadget is not None else "unconfigured",
                "configured": self._gadget is not None, "avatar_url": f"{self.core.base_url}/avatars/muse.png"}

    async def connect_steps(self, kind: str, principal: Principal, project: str | None,
                            choose: dict[str, Any] | None) -> dict[str, Any]:
        if not principal.is_unrestricted_owner:
            raise BlockRefused(OWNER_REQUIRED)
        args = choose or {}
        companion_id = args.get("companion")
        if set(args) - {"companion", "gadget"} or (
            "companion" in args and not (isinstance(companion_id, str) and re.fullmatch(r"cmp_[0-9a-f]{1,64}", companion_id))
        ):
            raise BlockError("Choose a companion ID from companion_list.")
        base = self.core.cfg.public_url
        if not base or urlsplit(base).scheme != "https":
            return {"done": [], "next": [{"by": "person", "do": public_https_prerequisite(self.core.cfg.owner_sign_in)}]}
        if not (sdk_token := self._token()):
            return {"done": [], "next": [
                {"by": "person", "do": "Create a personal Muse SDK token.", "link": SDK_TOKENS},
                self.token_step(principal, TOKEN_ENV, "Save the Muse SDK token.", label="Muse SDK token"),
            ]}
        done = ["Muse SDK token saved."]
        if self._pairing is None and self._gadget is not None and companion_id is None and not self._pair_window_closed:
            return {"done": [*done, "Muse gadget paired."], "next": [], "pairing": "paired"}
        companions = getattr(self.core, "companions", None)
        online = [row["id"] for row in companions.list() if row.get("online") and "muse" in row.get("capabilities", ())] if companions else []
        if self._pairing is None and companion_id is not None:
            if companion_id not in online:
                raise BlockError("Choose an online companion with the Muse capability.")
            self._pairing = (companion_id, self._identity())
            self._verified_pairing = self._pair_window_closed = False
            self.core.spawn(self._pair(companions, self._pairing, sdk_token), name="muse-pair")
        if self._pairing is not None:
            return {"done": done, "pairing": "in_progress", "next": [
                {"by": "person", "do": f"Open the Muse app. Turn on Developer mode in Settings > Devices. "
                                      f"Select Add Device and choose {self._pairing[1].ble_name}. "
                                      "If the app asks for Wi-Fi, select Use current connection."},
            ]}
        closed = {"pairing": "window_closed"} if self._pair_window_closed else {}
        if not online:
            return {"done": done, **closed, "next": [
                {"by": "you", "do": "Create a companion pairing code.", "call": {"tool": "companion_code", "arguments": {}}},
                {"by": "person", "do": f"Run uvx --from 'swb-one[companion]' swb companion connect {quote(base)} --cap muse. "
                                      "Enter the pairing code at the prompt."},
            ]}
        step = {"tool": "connect", "arguments": {"what": "muse", "choose": {"companion": online[0]}}}
        return {"done": done, **closed, "companions": online, "next": [
            {"by": "you", "do": "Retry pairing on the chosen companion." if closed else "Start pairing on the chosen companion.", "call": step},
        ]}

    async def _pair(self, companions: Any, pending: tuple[str, Identity], sdk_token: str) -> None:
        companion, ident = pending
        try:
            await companions.invoke(companion, "muse", "pair", {"sdk_token": sdk_token, "identity": {"mac": ident.mac},
                                                               "window_s": PAIRING_WINDOW}, timeout_s=660)
        except Exception:
            pass
        finally:
            self._pairing = None
            self._pair_window_closed = not self._verified_pairing

    async def _claim_pairing(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        from ..vendor.musegadget import muse_api, server_hosts

        rejected = {"ok": False, "error": "Muse could not verify the pairing."}
        async with self._claim_lock:
            pending = self._pairing
            if (pending is None or self._verified_pairing or principal.kind != "companion"
                    or principal.client_id != f"companion:{pending[0]}" or payload.get("node_id") != pending[1].setup_node_id):
                return rejected
            credentials = payload.get("credentials")
            if not isinstance(credentials, dict):
                return rejected
            record = {key: credentials.get(key, "") for key in CREDENTIALS}
            if any(not isinstance(value, str) or len(value) > 8192 or any(c in value for c in "\r\n\x00") for value in record.values()):
                return rejected
            if not (record["access_token"] and record["refresh_token"]
                    and server_hosts.api_allowed(record["api_url"]) and server_hosts.api_allowed(record["api_url_v2"])
                    and server_hosts.noise_allowed(record["noise_host"])):
                return rejected
            try:
                vms, status = await asyncio.to_thread(muse_api.fetch_vms_with_status, record["access_token"],
                                                       muse_api.api_root(record["api_url_v2"]))
            except Exception:
                return rejected
            if status != 200 or not vms or self._pairing is not pending:
                return rejected
            await self.aclose()
            if self._pairing is not pending:
                return rejected
            record.update(node_id=pending[1].setup_node_id, token_type="device", access_token_saved_at=int(self.core.clock()))
            self.core.claims.save_secret(PAIRING_SECRET, json.dumps(record))
            self._launch(pending[1], record)
            self._verified_pairing = True
            return {"ok": True}

    async def start(self) -> None:
        self.core.store.x(
            "UPDATE handles SET status = CASE WHEN EXISTS (SELECT 1 FROM inbox i WHERE i.handle = handles.handle "
            "AND i.kind = 'final') THEN 'answered' ELSE 'delivery_unknown' END, open = 0, updated_at = ? "
            "WHERE destination = 'muse' AND status = 'delivering'", (self.core.clock(),),
        )
        if record := self.core.claims.secret(PAIRING_SECRET):
            self._launch(self._identity(), json.loads(record))

    def _launch(self, ident: Identity, record: dict[str, Any]) -> None:
        from ..muse_gadget import MuseGadget

        def save_pairing(updated: dict[str, Any] | None) -> None:
            if self._gadget is gadget:
                self.core.claims.save_secret(PAIRING_SECRET, json.dumps(updated) if updated is not None else None)
                if updated is None:
                    self._gadget = None
                    self._tasks[1].cancel()

        gadget = MuseGadget(identity=ident, pairing=record, sdk_token=self._token(), save_pairing=save_pairing)
        self._gadget = gadget
        self._queued.set()
        self._tasks = (self.core.spawn(gadget.run(), name="muse-link"), self.core.spawn(self._drain(gadget), name="muse-delivery"))

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks = ()
        self._gadget = None

    async def targets(self) -> list[dict[str, Any]]:
        return [{"kind": "gadget", "id": "muse", "name": "Muse", "slugs": ["muse"],
                 "persona": {"id": "muse", "name": "Muse"}, "avatar_url": f"{self.core.base_url}/avatars/muse.png"}]

    def default_address(self) -> Address:
        return Address(self.name, "gadget", "muse") if self._gadget is not None else Address("agent", "agent", "muse")

    async def validate_route_target(self, target: dict[str, Any], defaults: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        if target != {"gadget": "muse"} or defaults:
            raise BlockError(f"Set the route target to {TARGET} and leave defaults empty.")
        return target, defaults

    def actions(self) -> list[Action]:
        return [Action("send", "Message Muse", "Queue text for the paired Muse gadget.", self.send, previewable=True),
                Action("result", "Muse conversation result", "Read the Muse reply.", self.result, read_only=True),
                Action("cancel", "Cancel a Muse conversation", "Stop following a Muse conversation.", self.cancel)]

    async def send(self, call: Call, *, task: str, target: str | None = None, project: str | None = None,
                   title: str | None = None, files: list[dict[str, Any]] | None = None, **_: Any) -> dict[str, Any]:
        if target not in (None, "muse", "gadget:muse"):
            raise BlockError(f"The Muse target is {TARGET}.")
        if call.principal.kind == "connect" or call.principal.trust != "owner":
            raise BlockRefused(OWNER_REQUIRED)
        if self._gadget is None:
            raise BlockError('Pair Muse with connect(what="muse").')
        task = task.strip()
        if not task or len(task) > MESSAGE_LIMIT or files:
            raise BlockError(f"Muse accepts text only, up to {MESSAGE_LIMIT} characters.")
        proj = self.core.project(project)
        slug = proj.slug if proj else None
        if not call.principal.allows_routing_scope(f"project:{slug}" if slug else "general"):
            raise BlockRefused(SCOPE_REQUIRED, "Choose a Muse send scope allowed by this key.")
        owner = call.principal.source
        if target is None and not self.core.routes.resolve(destination=self.name, agent=owner, project=slug)[0]:
            raise BlockError("Name Muse explicitly or configure a Muse route before sending.")
        out = {"to": TARGET, "project": slug}
        if call.dry_run:
            return out
        digest = hashlib.sha256(task.encode()).hexdigest()
        if call.handle is None and (row := self.core.store.one(
            "SELECT handle, status FROM handles WHERE owner = ? AND destination = 'muse' AND project IS ? "
            "AND created_at >= ? AND json_extract(meta, '$.muse_digest') = ? ORDER BY created_at DESC LIMIT 1",
            (owner, slug, self.core.clock() - DUPLICATE_WINDOW, digest),
        )):
            call.note(outcome="duplicate", ref=row["handle"], target=TARGET)
            return {**out, "handle": row["handle"], "queued": row["status"] == "queued", "duplicate": True}
        handle = self.core.inbox.open_handle(handle=call.handle, owner=owner, destination=self.name, ref=str(uuid4()),
                                             project=slug, title=title or "Muse", status="queued", target=TARGET,
                                             meta={**call.handle_meta, "muse_digest": digest, "muse_task": task})
        self._queued.set()
        call.note(ref=handle, target=TARGET)
        return {**out, "handle": handle, "queued": True}

    async def _drain(self, gadget: MuseGadget) -> None:
        from ..muse_gadget import WakeRejected, WakeUnavailable

        while True:
            await self._queued.wait()
            await gadget.wait_ready()
            row = self.core.store.one(
                "UPDATE handles SET status = 'delivering', updated_at = ? WHERE handle = "
                "(SELECT handle FROM handles WHERE destination = 'muse' AND status = 'queued' ORDER BY created_at, rowid LIMIT 1) "
                "RETURNING handle, ref, json_extract(meta, '$.muse_task') AS task",
                (self.core.clock(),),
            )
            if row is None:
                self._queued.clear()
                continue
            handle, state = row["handle"], "delivery_unknown"
            try:
                reply = await gadget.wake(row["ref"], row["task"])
                if reply and self.core.inbox.handle(handle)["status"] == "delivering":
                    trust, text = frame_sender(reply[:MESSAGE_LIMIT], "Muse", "muse")
                    await self.core.deliver(handle, "final", {"text": text, "trust": trust, "final": True,
                                                              "from": {"kind": "agent", "agent": "muse", "trust": trust}})
                    state = "answered"
                else:
                    state = "no_reply"
            except WakeUnavailable:
                state = "queued"
            except WakeRejected:
                state = "wake_rejected"
            except Exception:
                pass
            finally:
                self.core.store.x(
                    "UPDATE handles SET status = ?, open = ?, updated_at = ? WHERE handle = ? AND status = 'delivering'",
                    (state, int(state == "queued"), self.core.clock(), handle),
                )

    async def result(self, call: Call, *, ref: str, tail: int | None = None, **_: Any) -> dict[str, Any]:
        handle = self.core.inbox.handle(ref)
        replies = [json.loads(row["data"]) for row in self.core.store.q(
            "SELECT data FROM inbox WHERE handle = ? AND kind = 'final' ORDER BY id", (ref,))]
        return {"conversation": ref, "status": handle["status"], "final": replies[-1] if replies else None,
                "replies": replies[-(tail or 10):]}

    async def cancel(self, call: Call, *, ref: str) -> dict[str, Any]:
        self.core.inbox.update_handle(ref, status="cancelled", open=False)
        self.core.inbox.signal_waiters()
        return {"conversation": ref, "status": "cancelled"}
