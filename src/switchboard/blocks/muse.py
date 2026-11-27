"""A mailbox for Meta Muse, consumed by an owner-operated Linux gadget."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from shlex import quote
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..audit import iso
from ..auth import Principal
from ..claims import ClaimInputError
from ..muse_protocol import BRIDGE_PATH, CLAIM_ID, DELIVERY_TIMEOUT, MESSAGE_LIMIT, NO_REPLY, TOKEN, TOKEN_ENV, UNKNOWN
from . import Action, Address, AgentSpec, Block, BlockError, Call, Manifest, TargetKind

if TYPE_CHECKING:
    from ..core import Switchboard

MAILBOX = "muse"
TARGET = "muse:gadget:muse"
SDK_DOCS = "https://github.com/facebookincubator/muse-gadget-sdk/tree/main/linux"
SDK_TOKENS = "https://gadgets.muse.ai/settings/sdk-tokens"
SDK_TERMS = "https://gadgets.muse.ai/sdk-terms"
DUPLICATE_WINDOW = 600
BRIDGE_PRINCIPAL = Principal(MAILBOX, "muse-gadget", "muse_bridge", origin="http", scopes=(), tools=(), targets=())


def validate_bridge_token(value: str) -> None:
    if not TOKEN.fullmatch(value):
        raise ClaimInputError("Paste the bridge token from Muse.")


class MuseBlock(Block):
    connect_kinds = ("muse",)
    wakes_agents = True
    manifest = Manifest(
        id="muse",
        title="Muse",
        kind="both",
        level_in="L3",
        level_out="L3",
        verbs={"send": "send"},
        agents=(AgentSpec(MAILBOX, "Muse"),),
        targets=(TargetKind("gadget", persona=True),),
        secrets=(TOKEN_ENV,),
        instructions="Send text to muse to wake the owner's paired Muse gadget. The reply returns on the mailbox conversation.",
    )

    def __init__(self, core: Switchboard, config: dict[str, Any]):
        super().__init__(core, config)
        if self.name != MAILBOX:
            raise BlockError("Configure the Muse gadget under blocks.muse.")
        core.claims.register_name(TOKEN_ENV, validate_bridge_token)
        core.store.x("CREATE TABLE IF NOT EXISTS muse_bridge (id INTEGER PRIMARY KEY CHECK(id = 1), key_hash TEXT, "
                     "last_seen REAL, last_reply REAL)")
        core.store.x("CREATE UNIQUE INDEX IF NOT EXISTS muse_claim ON handles(json_extract(meta, '$.muse_claim')) "
                     "WHERE json_extract(meta, '$.muse_claim') IS NOT NULL")
        self._task: asyncio.Task[None] | None = None

    def credential(self) -> str | None:
        token = self.core.secret(TOKEN_ENV)
        return token if token and TOKEN.fullmatch(token) else None

    def verify_bridge(self, token: str | None) -> Principal | None:
        secret = self.credential()
        if secret and token and TOKEN.fullmatch(token) and hmac.compare_digest(secret, token):
            return BRIDGE_PRINCIPAL
        return None

    def _state(self) -> dict[str, Any]:
        secret = self.credential()
        row = self.core.store.one("SELECT key_hash, last_seen, last_reply FROM muse_bridge WHERE id = 1")
        current = row if secret and row and row["key_hash"] == hashlib.sha256(secret.encode()).hexdigest() else None
        seen = current["last_seen"] if current else None
        reply = current["last_reply"] if current else None
        online = seen is not None and self.core.clock() - seen < DELIVERY_TIMEOUT
        return {"state": "online" if online else "offline" if secret else "unconfigured",
                "configured": bool(secret), "last_seen": iso(seen) if seen is not None else None,
                "verified": reply is not None, "avatar_url": f"{self.core.base_url}/avatars/muse.png"}

    @property
    def online(self) -> bool:
        return self._state()["state"] == "online"

    def status(self) -> dict[str, Any]:
        return self._state()

    def config_step(self) -> str | None:
        if not self.credential():
            return 'Run connect(what="muse") for a bridge token claim link.'
        return None

    def next_step(self) -> str | None:
        state = self._state()
        if not state["configured"]:
            return self.config_step()
        if state["state"] != "online":
            return "Start the SwB bridge on your paired Linux Muse gadget."
        if not state["verified"]:
            return "Send a test message to Muse and check the conversation for a reply."
        return None

    def page_rows(self) -> list[tuple[str, str, str, bool | None]]:
        state = self._state()
        return [("Muse", "", state["state"], state["state"] == "online" and state["verified"])]

    async def connect_steps(self, kind: str, principal: Principal, project: str | None,
                            choose: dict[str, Any] | None) -> dict[str, Any]:
        args = choose or {}
        if set(args) - {"replace"} or ("replace" in args and not isinstance(args["replace"], bool)):
            raise BlockError("Muse setup accepts only choose.replace (true to rotate the bridge token).")
        state = self._state()
        done = [{"unconfigured": "Muse bridge setup is ready.", "offline": "Muse bridge token saved.",
                 "online": "Muse bridge online."}[state["state"]]] + (["Muse has returned a reply."] if state["verified"] else [])
        steps = []
        if not state["configured"] or args.get("replace"):
            if self.core.cfg.env.get(TOKEN_ENV):
                steps.append({"by": "person", "do": f"Set {TOKEN_ENV} in .env to a random token of 43 to 128 letters, digits, hyphens, or underscores. Restart SwB."})
            else:
                steps.append(self.token_step(principal, TOKEN_ENV,
                             "Use a password manager to generate a bridge token with 64 random letters and digits. Save it through this link."))
        if steps or state["state"] != "online":
            steps.append({"by": "person", "do": f"Read {SDK_TERMS}. Create a personal Meta token.", "link": SDK_TOKENS})
            steps.append({"by": "person", "do": f"Install Meta's Linux Device SDK on a device with Bluetooth Low Energy. Run swb muse-bridge --url {quote(self.core.base_url)} --setup. Enter both tokens at the hidden prompts. In the Muse app, turn on Settings > Devices > Developer mode. Choose Add Device. Select the Bluetooth name SwB prints. Leave SwB running.",
                          "link": SDK_DOCS})
        elif not state["verified"]:
            steps.append({"by": "you", "do": "Send the test message. Check the conversation with result.",
                          "call": {"tool": "send", "arguments": {"to": TARGET, "task": "Reply with pong to test SwB."}}})
        return {"done": done, "next": steps}

    async def targets(self) -> list[dict[str, Any]]:
        return [{"kind": "gadget", "id": MAILBOX, "name": "Muse", "slugs": [MAILBOX],
                 "persona": {"id": MAILBOX, "name": "Muse"}, "avatar_url": f"{self.core.base_url}/avatars/muse.png"}]

    def default_address(self) -> Address:
        return Address(self.name, "gadget", MAILBOX)

    async def validate_route_target(self, target: dict[str, Any], defaults: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        if target != {"gadget": MAILBOX} or defaults:
            raise BlockError(f"Set the route target to {TARGET} and leave defaults empty.")
        return target, defaults

    def actions(self) -> list[Action]:
        return [Action("send", "Message Muse", "Queue text for the owner's paired Muse gadget.", self.send)]

    async def send(self, call: Call, *, task: str, target: str | None = None, project: str | None = None,
                   files: list[dict[str, Any]] | None = None, **_: Any) -> dict[str, Any]:
        if target not in (None, MAILBOX, f"gadget:{MAILBOX}"):
            raise BlockError(f"The Muse target is {TARGET}.")
        if call.principal.kind == "connect" or call.principal.trust != "owner":
            raise BlockError("Use the owner's sign-in to wake Muse.")
        if not self.credential():
            raise BlockError('Set up Muse with connect(what="muse").')
        task = task.strip()
        if not task or len(task) > MESSAGE_LIMIT or files:
            raise BlockError(f"Muse accepts text only, up to {MESSAGE_LIMIT} characters.")
        proj = self.core.project(project)
        slug = proj.slug if proj else None
        if not call.principal.allows(f"project:{slug}" if slug else "general"):
            raise BlockError("Choose a Muse send scope allowed by this key.")
        if target is None and not self.core.routes.resolve(destination=self.name, agent=call.principal.source, project=slug)[0]:
            raise BlockError("Name Muse explicitly or configure a Muse route before sending.")
        digest = hashlib.sha256(task.encode()).hexdigest()
        if call.handle is None and (row := self.core.store.one(
            "SELECT handle FROM handles WHERE owner = ? AND destination = 'mailbox' AND ref = ? AND project IS ? "
            "AND created_at >= ? AND json_extract(meta, '$.muse_digest') = ? ORDER BY created_at DESC LIMIT 1",
            (MAILBOX, call.principal.source, slug, self.core.clock() - DUPLICATE_WINDOW, digest),
        )):
            call.note(outcome="duplicate", ref=row["handle"], target=TARGET)
            return {"to": TARGET, "handle": row["handle"], "mailbox": True, "queued": True, "duplicate": True, "project": slug}
        out = await self.core.agent_send(
            call.principal, to=MAILBOX, text=task, project=slug, handle=call.handle,
            handle_meta={**call.handle_meta, "muse_digest": digest, "muse_session": str(uuid4())},
        )
        call.note(ref=out["handle"], target=TARGET)
        return {**out, "to": TARGET, "queued": True}

    def _expire(self) -> None:
        self.core.store.x(
            "UPDATE handles SET status = ?, updated_at = ? WHERE owner = ? AND destination = 'mailbox' "
            "AND status = 'delivering' AND json_extract(meta, '$.muse_claim') IS NOT NULL AND updated_at <= ?",
            (UNKNOWN, self.core.clock(), MAILBOX, self.core.clock() - DELIVERY_TIMEOUT),
        )

    async def start(self) -> None:
        async def sweep() -> None:
            while True:
                self._expire()
                await asyncio.sleep(15)

        self._task = asyncio.create_task(sweep(), name="muse-delivery")

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def _pickup(self, claim: str) -> dict[str, Any] | None:
        def take(db: Any) -> dict[str, Any] | None:
            row = db.execute(
                "SELECT handle, status FROM handles WHERE owner = ? AND destination = 'mailbox' "
                "AND json_extract(meta, '$.muse_claim') = ?", (MAILBOX, claim),
            ).fetchone()
            if row is None:
                row = db.execute(
                    "UPDATE handles SET status = 'delivering', updated_at = ?, meta = json_set(meta, '$.muse_claim', ?) "
                    "WHERE handle = (SELECT h.handle FROM handles h JOIN inbox i ON i.handle = h.handle "
                    "WHERE h.owner = ? AND h.destination = 'mailbox' AND h.status = 'delivered' "
                    "AND json_extract(h.meta, '$.muse_session') IS NOT NULL AND i.owner = h.owner AND i.kind = 'message' "
                    "ORDER BY i.id LIMIT 1) RETURNING handle, status", (self.core.clock(), claim, MAILBOX),
                ).fetchone()
            if row is None or row["status"] != "delivering":
                return None
            message = db.execute(
                "SELECT json_extract(h.meta, '$.muse_session') AS session_id, json_extract(i.data, '$.text') AS text "
                "FROM handles h JOIN inbox i ON i.handle = h.handle WHERE h.handle = ? AND i.kind = 'message' ORDER BY i.id LIMIT 1",
                (row["handle"],),
            ).fetchone()
            return {"handle": row["handle"], **dict(message)}

        self._expire()
        return self.core.store.tx(take)

    def routes(self) -> list[Route]:
        return [Route(BRIDGE_PATH, self.bridge, methods=["POST"])]

    async def bridge(self, request: Request) -> JSONResponse:
        from .mcp import principal_of
        from ..core import CoreError

        def respond(data: dict[str, Any], code: int = 200) -> JSONResponse:
            return JSONResponse(data, status_code=code, headers={"Cache-Control": "no-store"})

        if principal_of(request) is not BRIDGE_PRINCIPAL:
            return respond({"error": "unauthorized"}, 401)
        try:
            body = await request.json()
        except (ValueError, UnicodeError):
            return respond({"error": "invalid JSON"}, 400)
        claim = body.get("claim") if isinstance(body, dict) else None
        if not isinstance(claim, str) or not CLAIM_ID.fullmatch(claim):
            return respond({"error": "invalid claim"}, 400)
        token = request.headers["authorization"][7:].strip()
        key_hash = hashlib.sha256(token.encode()).hexdigest()
        self.core.store.x(
            "INSERT INTO muse_bridge (id, key_hash, last_seen) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET key_hash = excluded.key_hash, last_seen = excluded.last_seen, "
            "last_reply = CASE WHEN key_hash = excluded.key_hash THEN last_reply ELSE NULL END",
            (key_hash, self.core.clock()),
        )
        if "handle" not in body:
            return respond({"message": self._pickup(claim)})
        handle, state, text = body["handle"], body.get("state"), body.get("text")
        if not isinstance(handle, str) or len(handle) > 40 or state not in ("answered", NO_REPLY, UNKNOWN):
            return respond({"error": "invalid result"}, 400)
        if state == "answered" and (not isinstance(text, str) or not text.strip() or len(text) > MESSAGE_LIMIT):
            return respond({"error": "invalid reply"}, 400)
        h = self.core.inbox.handle(handle)
        if (h is None or h["owner"] != MAILBOX or h["destination"] != "mailbox" or h["meta"].get("muse_claim") != claim):
            return respond({"error": "no such delivery"}, 404)
        if h["status"] != "delivering" and not (h["status"] == UNKNOWN and state != UNKNOWN):
            return respond({"ok": True})
        if state == "answered":
            try:
                answer = "h_" + hashlib.sha256(f"muse-answer:{handle}".encode()).hexdigest()[:32]
                if self.core.store.one("SELECT 1 FROM inbox WHERE owner = ? AND handle = ? LIMIT 1", (h["ref"], answer)):
                    self.core.inbox.update_handle(handle, status="answered")
                else:
                    self.core.store.x(
                        "DELETE FROM handles WHERE handle = ? AND owner = ? AND ref = ? AND destination = 'mailbox' "
                        "AND json_extract(meta, '$.in_reply_to') = ?", (answer, h["ref"], MAILBOX, handle),
                    )
                    await self.core.agent_send(BRIDGE_PRINCIPAL, to=h["ref"], text=text, project=h["project"],
                                               in_reply_to=handle, handle=answer, _reply=True)
            except CoreError:
                return respond({"error": "conversation cannot accept a reply"}, 409)
            self.core.store.x("UPDATE muse_bridge SET last_reply = ? WHERE id = 1 AND key_hash = ?", (self.core.clock(), key_hash))
        else:
            self.core.inbox.update_handle(handle, status=state)
        return respond({"ok": True})
