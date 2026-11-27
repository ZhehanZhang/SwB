from __future__ import annotations

import re
from dataclasses import dataclass
from functools import partial
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from ..audit import iso
from ..auth import Principal
from ..companion.caps.base import CapabilityError
from ..companion.harness import STATES, WAKE_TTL_S, Session, reply_text
from ..companions import COMPANION_ID, OFFLINE, CompanionError, DeviceActionError
from ..refusals import TARGET_REQUIRED
from ..targets import NOT_TARGETS, SLUG_RE
from ..trust import FramedText, frame_sender, neutralize
from ..visibility import Who
from . import Action, Address, Block, BlockError, BlockRefused, Call, Manifest, TargetKind

if TYPE_CHECKING:
    from ..core import Switchboard

CAPS = ("hooks", "herdr", "codex", "opencode", "dsh")
AGENTS = {
    "claude": "claude-code", "claude-code": "claude-code", "codex": "codex", "gemini": "gemini", "opencode": "opencode",
    "dsh": "dsh",
}
SESSION_TTL_S = 86_400
WAKE_TIMEOUT_S = 10
SESSION_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,159}")
SessionKey = tuple[str, str, str]
REPLY_UNAVAILABLE = "Local wake reply unavailable."
REPLY_SESSION = "Use the original local session."
REPLY_DUPLICATE = "A local reply already exists."
REPLY_DELIVERY = "A local reply requires an active wake."
REPLY_CONVERSATION = "Use a known local conversation."


@dataclass(frozen=True)
class Reported:
    cap: str
    value: Session
    at: float


@dataclass
class Pending:
    companion: str
    cap: str
    session: str
    agent: str
    at: float
    confirmed: bool = False
    early: str = ""
    relaying: bool = False


def _label(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    value = neutralize(value)
    leaf = value.replace("\\", "/").rsplit("/", 1)[-1]
    return "".join(c for c in leaf if c.isprintable())[:limit]


def _session_key(ident: str) -> SessionKey | None:
    head, _, tail = ident.partition(":")
    if COMPANION_ID.fullmatch(head):
        companion = head
        harness, _, session = tail.partition(":")
    else:
        companion, harness, session = "", head, tail
    if harness in (*AGENTS, "herdr") and SESSION_ID.fullmatch(session):
        return companion, harness, session
    return None


class LocalBlock(Block):
    cache_targets = False
    manifest = Manifest(id="local", title="Local sessions", kind="sink", level_out="L2",
                        verbs={"send": "wake"}, targets=(TargetKind("session", slug_from="name"),))

    def __init__(self, core: Switchboard, config: dict[str, Any]):
        super().__init__(core, config)
        self.sessions: dict[SessionKey, Reported] = {}
        self.offline: dict[SessionKey, tuple[str, float]] = {}
        self.wakes: dict[str, Pending] = {}
        for cap in CAPS:
            core.companions.register_event(f"{cap}.session", partial(self._session, cap), capability=cap)
            core.companions.register_event(f"{cap}.reply", partial(self._reply, cap), capability=cap)
        core.companions.on_disconnect.append(self._disconnect)
        if self.name not in core.blocks and core.companions.has_session_claims():
            core.blocks[self.name] = self

    @property
    def online(self) -> bool:
        self._expire()
        return bool(self.sessions)

    @property
    def hidden(self) -> bool:
        return not self.online

    def _expire(self) -> None:
        now = self.core.clock()
        for key in [key for key, row in self.sessions.items() if now - row.at >= SESSION_TTL_S]:
            del self.sessions[key]
        for key in [key for key, (_, at) in self.offline.items() if now - at >= SESSION_TTL_S]:
            del self.offline[key]
        for key in [key for key, row in self.wakes.items() if now - row.at >= WAKE_TTL_S]:
            del self.wakes[key]

    def _disconnect(self, companion: str) -> None:
        keys = [key for key in self.sessions if key[0] == companion]
        for key in keys:
            row = self.sessions.pop(key)
            self.offline[key] = (row.value.agent, row.at)
        if keys:
            self.core.changed()

    async def _session(self, cap: str, principal: Principal, payload: dict[str, Any]) -> None:
        companion = principal.client_id.removeprefix("companion:")
        harness, session, state = (payload.get(k) for k in ("harness", "session", "state"))
        allowed = AGENTS if cap == "hooks" else (cap,)
        if (not isinstance(harness, str) or harness not in allowed or not isinstance(session, str)
                or not SESSION_ID.fullmatch(session) or state not in STATES):
            raise CompanionError("Invalid local session report.")
        if not self.core.companions.connected(companion):
            raise CompanionError("Open the companion event stream first.", 409)
        self._expire()
        key = companion, harness, session
        self.core.companions.claim_session(*key, SESSION_TTL_S, ending=state == "ended")
        previous = self.sessions.get(key)
        if state == "ended":
            if previous:
                del self.sessions[key]
                self.offline[key] = (previous.value.agent, self.core.clock())
                self.core.changed()
            return
        self.offline.pop(key, None)
        agent = _label(payload.get("agent"), 32).lower()
        value = Session(harness, session, agent, _label(payload.get("name"), 64), _label(payload.get("dir"), 64), state,
                        _label(payload.get("detail"), 120))
        carrier = previous.cap if cap == "hooks" and previous else cap
        if self.name not in self.core.blocks:
            self.core.blocks = {**self.core.blocks, self.name: self}
        self.sessions[key] = Reported(carrier, value, self.core.clock())
        if previous is None or previous.value != value or carrier != previous.cap:
            self.core.changed()

    def _agent(self, harness: str, kind: str = "") -> str | None:
        agent = AGENTS.get(harness) or AGENTS.get(kind, kind)
        return agent if SLUG_RE.fullmatch(agent) and agent not in NOT_TARGETS and agent != "owner" and agent in self.core.known_agents() else None

    def _find(self, key: SessionKey) -> tuple[SessionKey, Reported | None, str | None, bool]:
        self._expire()
        companion, harness, session = key
        if not companion:
            if owner := self.core.companions.session_owner(harness, session):
                key = owner, harness, session
        row = self.sessions.get(key)
        if row:
            return key, row, self._agent(harness, row.value.agent), True
        if not key[0] or key[0] != self.core.companions.session_owner(harness, session):
            return key, None, None, False
        offline = self.offline.get(key)
        return key, None, self._agent(harness, offline[0] if offline else ""), True

    def canonical_target(self, addr: Address) -> Address:
        if addr.kind == "session" and (key := _session_key(addr.id)) is not None:
            key, _, _, _ = self._find(key)
            if key[0]:
                return Address(self.name, addr.kind, ":".join(key))
        return addr

    def grant_target(self, addr: Address) -> Address | None:
        key = _session_key(addr.id) if addr.kind == "session" else None
        agent = self._find(key)[2] if key else None
        return Address("agent", "agent", agent) if agent else None

    def _rows(self, *, framed: bool) -> list[dict[str, Any]]:
        self._expire()
        rows = []
        for key, row in self.sessions.items():
            value = row.value
            ident = ":".join(key)
            label = value.name or value.dir
            agent = self._agent(value.harness, value.agent)
            slug = f"local-{sha256(ident.encode()).hexdigest()[:16]}"
            fields = {"name": label, "dir": value.dir, "detail": value.detail}
            if framed:
                fields = {field: frame_sender(text, None, key[0], "local session")[1] if text else text
                          for field, text in fields.items()}
            fields["name"] = fields["name"] or value.session
            rows.append({"kind": "session", "id": ident, "harness": value.harness, **fields,
                         "state": value.state, "slugs": [slug], "agent": agent, "trust": "external",
                         "last_seen_at": iso(row.at), **({"persona": {"id": agent}} if agent else {})})
        return rows

    async def targets(self) -> list[dict[str, Any]]:
        return self._rows(framed=True)

    def page_rows(self) -> list[tuple[str, str, str, bool | None]]:
        return [(row["name"], row["harness"], row["state"], row["state"] not in ("blocked", "unknown")) for row in self._rows(framed=False)]

    def actions(self) -> list[Action]:
        return [Action("wake", "Wake a local session", "Send a message to a local session.", self.wake, previewable=True)]

    async def wake(self, call: Call, *, task: str, target: str = "", project: str | None = None, title: str | None = None,
                   **_: Any) -> dict[str, Any]:
        kind, _, ident = target.partition(":")
        key = _session_key(ident)
        if kind != "session" or key is None:
            raise BlockError("Choose a local session address.")
        key, initial, agent, known = self._find(key)
        companion, _, session = key
        principal = call.principal
        to = f"{self.name}:{kind}:{':'.join(key) if companion else ident}"
        who = principal.visibility_who or Who(owner=principal.trust == "owner")
        if not who.owner:
            if (not known or (initial is not None and not agent)
                    or not self.core.visibility.allows(who, f"target:{to}")):
                raise BlockRefused(TARGET_REQUIRED, "Access to this local session is restricted.")
        call.note(target=to)
        if initial is None:
            raise BlockError("Local session is offline.")
        mailbox = agent or "owner"
        publish = self.core.visibility.allows(who, f"target:{mailbox}")
        out = await self.core.agent_send(principal, to=mailbox, text=task, project=project, allow_self=True,
                                         handle=call.handle, handle_meta=call.handle_meta, title=title, dry_run=call.dry_run,
                                         _allow_unregistered=agent is None, _session_target=None if publish else to)
        base = {**out, "to": to, "agent": mailbox}
        if call.dry_run:
            return base
        conversation = out["handle"]
        call.note(ref=conversation)
        _, row, current_agent, _ = self._find(key)
        reason = None
        if row is None:
            reason = OFFLINE
        elif row.cap == "hooks":
            reason = "Hook sessions receive messages through the mailbox."
        elif not agent:
            reason = "No agent mailbox matches this session."
        elif agent != current_agent:
            reason = "The session's agent changed."
        elif row.value.state == "blocked":
            reason = "Agent is waiting on a prompt."
        if reason:
            if not publish:
                self.core.inbox.update_handle(conversation, status="cancelled")
                raise BlockError(reason)
            call.note(outcome="mailbox")
            return {**base, "woke": False, "note": f"{reason} Message left in the {mailbox} mailbox."}
        sender = self.core.agents.label(principal.source)
        body = task if isinstance(task, FramedText) else task.strip()[:20_000]
        _, text = frame_sender(body, sender, principal.source, owner=principal.trust == "owner")
        text += (f'\n\n---\nSwB delivered this message from {sender}. Reply on this conversation with '
                 f'reply(conversation="{conversation}", text=<answer>, final=true) if the SwB connector is available. '
                 'Otherwise, give your answer here.')
        pending = self.wakes[conversation] = Pending(companion, row.cap, session, agent, self.core.clock())
        try:
            await self.core.companions.invoke(companion, row.cap, "prompt", {"session": session, "text": text,
                                                                            "conversation": conversation, "agent": agent},
                                              timeout_s=WAKE_TIMEOUT_S, principal=principal)
        except (CompanionError, CapabilityError) as exc:
            reason = exc.display if isinstance(exc, DeviceActionError) else str(exc)
            if not publish:
                self.core.inbox.update_handle(conversation, status="cancelled")
                raise BlockError(reason) from exc
            call.note(outcome="mailbox")
            return {**base, "woke": False, "note": f"{reason}\nMessage left in the {mailbox} mailbox."}
        else:
            pending.confirmed = True
        finally:
            if not pending.confirmed:
                self.wakes.pop(conversation, None)
        if pending.early:
            pending.relaying = True
            try:
                await self._relay(conversation, pending, pending.early)
            except CompanionError as exc:
                self.core.audit.record_refusal(origin="companion", client=f"companion:{companion}", destination="core",
                                              action="companion_emit", target=companion, detail=exc.audit_detail)
            finally:
                self.wakes.pop(conversation, None)
        return {**base, "delivered": True, "woke": True}

    async def _reply(self, cap: str, principal: Principal, payload: dict[str, Any]) -> None:
        conversation, text = payload.get("conversation"), payload.get("text")
        self._expire()
        if not isinstance(conversation, str) or not isinstance(text, str) or not text.strip():
            raise CompanionError("Invalid local wake reply.")
        pending = self.wakes.get(conversation)
        if pending is None:
            handle = self.core.inbox.handle(conversation)
            if handle is None:
                reason = REPLY_CONVERSATION
            elif handle["state"].get("local_reply_relayed"):
                reason = REPLY_DUPLICATE
            else:
                reason = REPLY_DELIVERY
            raise CompanionError(REPLY_UNAVAILABLE, 404, audit_detail=reason)
        if (principal.client_id != f"companion:{pending.companion}" or cap not in (pending.cap, "hooks")
                or payload.get("session") != pending.session or payload.get("agent") != pending.agent):
            raise CompanionError(REPLY_UNAVAILABLE, 404, audit_detail=REPLY_SESSION)
        text = reply_text(text)
        if not text.strip():
            raise CompanionError("Invalid local wake reply.")
        if pending.early or pending.relaying:
            raise CompanionError(REPLY_UNAVAILABLE, 409, audit_detail=REPLY_DUPLICATE)
        self._check_reply(conversation, pending)
        if not pending.confirmed:
            pending.early = text
            return
        pending.relaying = True
        try:
            await self._relay(conversation, pending, text)
            self.wakes.pop(conversation, None)
        finally:
            pending.relaying = False

    def _check_reply(self, conversation: str, pending: Pending) -> None:
        handle = self.core.inbox.handle(conversation)
        if (handle is None or handle["owner"] != pending.agent or handle["status"] == "cancelled"
                or self.core.clock() - pending.at >= WAKE_TTL_S):
            raise CompanionError(REPLY_UNAVAILABLE, 409, audit_detail=REPLY_DELIVERY)
        if handle["status"] == "answered":
            raise CompanionError(REPLY_UNAVAILABLE, 409, audit_detail=REPLY_DUPLICATE)

    async def _relay(self, conversation: str, pending: Pending, text: str) -> None:
        self._check_reply(conversation, pending)
        principal = Principal(pending.agent, f"companion:{pending.companion}", "companion", origin="companion", trust="device")
        if not (await self.core.verb(principal, "reply", conversation, text=text, final=True))["delivered"]:
            raise CompanionError(REPLY_UNAVAILABLE, 409, audit_detail=REPLY_DELIVERY)
        self.core.inbox.update_handle(conversation, state={"local_reply_relayed": True})
