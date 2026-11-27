from __future__ import annotations

import re
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from ..audit import iso
from ..auth import Principal
from ..companion.caps.base import CapabilityError
from ..companion.harness import STATES, WAKE_TTL_S, Session, reply_text
from ..companions import OFFLINE, CompanionError
from ..refusals import TARGET_REQUIRED
from ..targets import NOT_TARGETS, SLUG_RE, slugify
from ..trust import FramedText, frame_sender
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
SESSION_ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}")
SessionKey = tuple[str, str, str]


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


def _label(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    leaf = value.replace("\\", "/").rsplit("/", 1)[-1]
    return "".join(c for c in leaf if c.isprintable())[:limit]


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
                or not SESSION_ID.fullmatch(session) or state not in STATES or not self.core.companions.connected(companion)):
            return
        self._expire()
        key = companion, harness, session
        self.offline.pop(key, None)
        previous = self.sessions.get(key)
        if state == "ended":
            if self.sessions.pop(key, None):
                self.core.changed()
            return
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

    def _find(self, ident: str) -> tuple[list[tuple[str, Reported]], str | None, str | None]:
        self._expire()
        harness, _, session = ident.partition(":")
        live = [(key[0], row) for key, row in self.sessions.items() if key[1:] == (harness, session)]
        offline = [kind for key, (kind, _) in self.offline.items() if key[1:] == (harness, session)]
        agents = {self._agent(harness, row.value.agent) for _, row in live} or {self._agent(harness, kind) for kind in offline}
        agent = next(iter(agents)) if len(agents) == 1 else self._agent(harness) if not agents else None
        reason = ("Multiple companions reported this session." if len(live) > 1 else None if live
                  else OFFLINE if offline else "Local session unavailable.")
        return live, agent, reason

    def grant_target(self, addr: Address) -> Address | None:
        agent = self._find(addr.id)[1] if addr.kind == "session" else None
        return Address("agent", "agent", agent) if agent else None

    def _rows(self) -> list[dict[str, Any]]:
        self._expire()
        rows = {}
        for row in self.sessions.values():
            value = row.value
            ident = f"{value.harness}:{value.session}"
            name = value.name or value.dir or value.session
            agent = self._agent(value.harness, value.agent)
            if ident in rows:
                rows[ident].update(state="unknown", detail="Multiple companions reported this session.")
                continue
            rows[ident] = {"kind": "session", "id": ident, "harness": value.harness, "name": name, "dir": value.dir,
                           "state": value.state, "detail": value.detail, "slugs": [slugify(name)], "agent": agent,
                           "last_seen_at": iso(row.at), **({"persona": {"id": agent}} if agent else {})}
        return list(rows.values())

    async def targets(self) -> list[dict[str, Any]]:
        return self._rows()

    def page_rows(self) -> list[tuple[str, str, str, bool | None]]:
        return [(row["name"], row["harness"], row["state"], row["state"] not in ("blocked", "unknown")) for row in self._rows()]

    def actions(self) -> list[Action]:
        return [Action("wake", "Wake a local session", "Send a message to a local session.", self.wake, previewable=True)]

    async def wake(self, call: Call, *, task: str, target: str = "", project: str | None = None, title: str | None = None,
                   **_: Any) -> dict[str, Any]:
        kind, _, ident = target.partition(":")
        harness, _, session = ident.partition(":")
        if kind != "session" or harness not in (*AGENTS, "herdr") or not SESSION_ID.fullmatch(session):
            raise BlockError("Choose a local session address.")
        initial, agent, _ = self._find(ident)
        principal = call.principal
        to = f"{self.name}:{target}"
        who = principal.visibility_who or Who(owner=principal.trust == "owner")
        if not who.owner:
            resources = self.core.target_visibility_resources(to, Address(self.name, kind, ident),
                                                              slugs=self.core.slug_map(await self.core.targets(block=self.name)))
            if not agent or not self.core.visibility.allows(who, f"target:{agent}") or not self.core.visibility.allows(who, *resources):
                raise BlockRefused(TARGET_REQUIRED, "Access to this local session is restricted.")
        mailbox = agent or "owner"
        out = await self.core.agent_send(principal, to=mailbox, text=task, project=project, allow_self=True,
                                         handle=call.handle, handle_meta=call.handle_meta, title=title, dry_run=call.dry_run,
                                         _allow_unregistered=agent is None)
        base = {**out, "to": to, "agent": mailbox}
        if call.dry_run:
            return base
        conversation = out["handle"]
        call.note(target=to, ref=conversation)
        live, current_agent, reason = self._find(ident)
        if not reason:
            companion, row = live[0]
            if row.cap == "hooks":
                reason = "Hook sessions receive messages through the mailbox."
            elif not agent:
                reason = "No agent mailbox matches this session."
            elif agent != current_agent:
                reason = "The session's agent changed."
            elif len(initial) != 1 or initial[0][0] != companion:
                reason = "The session's companion changed."
            elif row.value.state == "blocked":
                reason = "Agent is waiting on a prompt."
        if reason:
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
                                              principal=principal)
        except (CompanionError, CapabilityError) as exc:
            call.note(outcome="mailbox")
            return {**base, "woke": False, "note": f"{exc} Message left in the {mailbox} mailbox."}
        else:
            pending.confirmed = True
        finally:
            if not pending.confirmed:
                self.wakes.pop(conversation, None)
        if pending.early:
            self.wakes.pop(conversation, None)
            await self._relay(conversation, pending, pending.early)
        return {**base, "woke": True}

    async def _reply(self, cap: str, principal: Principal, payload: dict[str, Any]) -> None:
        conversation, text = payload.get("conversation"), payload.get("text")
        self._expire()
        if not isinstance(conversation, str) or not isinstance(text, str) or not text.strip():
            return
        pending = self.wakes.get(conversation)
        if (pending is None or principal.client_id != f"companion:{pending.companion}" or cap not in (pending.cap, "hooks")
                or payload.get("session") != pending.session or payload.get("agent") != pending.agent):
            return
        text = reply_text(text)
        if not text.strip():
            return
        if not pending.confirmed:
            pending.early = pending.early or text
            return
        await self._relay(conversation, pending, text)
        if self.wakes.get(conversation) is pending:
            del self.wakes[conversation]

    async def _relay(self, conversation: str, pending: Pending, text: str) -> None:
        handle = self.core.inbox.handle(conversation)
        if (handle is None or handle["owner"] != pending.agent or handle["status"] == "cancelled"
                or self.core.clock() - pending.at >= WAKE_TTL_S):
            return
        if any(reply["final"] and (reply.get("from") or {}).get("agent") == pending.agent
               for reply in self.core.mailbox_replies(conversation, handle["ref"])):
            return
        principal = Principal(pending.agent, f"companion:{pending.companion}", "companion", origin="companion", trust="device")
        await self.core.verb(principal, "reply", conversation, text=text, final=True)
