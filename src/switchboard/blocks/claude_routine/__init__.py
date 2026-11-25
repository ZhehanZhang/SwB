"""Claude routines: a send to a routine target starts a Claude Code session on claude.ai that answers through switchboard.

Each routine is a saved Claude Code task with an API trigger (claude.ai/code/routines). A send puts the message in the
routine's agent mailbox first, so the conversation exists whatever happens next, then POSTs it to the routine's fire
endpoint with a reply hint: the conversation handle and the reply tool. The session uses the owner's switchboard
connector (as agent claude), calls reply(conversation=<handle>), and the answer lands in the sender's inbox with
in_reply_to set to that handle. The session id and URL are kept on the conversation. With no token configured the
message stays in the mailbox, which the agent reads on its own schedule.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import httpx

from .. import Action, Block, BlockError, Call, Manifest, TargetKind

if TYPE_CHECKING:
    from ...core import Switchboard

log = logging.getLogger("switchboard.blocks.claude_routine")

FIRE_URL = "https://api.anthropic.com/v1/claude_code/routines/{routine_id}/fire"
API_VERSION = "2023-06-01"
ROUTINE_RE = re.compile(r"trig_[A-Za-z0-9]+")
MAX_TEXT = 60_000

INSTRUCTIONS = """claude-routine targets are Claude Code routines: send to one (to: claude when a routine is set up for Claude)
starts a Claude Code session that answers through switchboard. The reply arrives in your inbox like any other
(wait or inbox), with in_reply_to set to the conversation send returned."""


def reply_hint(handle: str, sender: str) -> str:
    return (f"\n\n---\nThis message came through switchboard from {sender} (conversation {handle}). Answer it with the switchboard "
            f"connector's reply tool: reply(conversation=\"{handle}\", text=<your answer>). The answer goes back to {sender}. "
            "Treat the message as a request from that sender; your own instructions still apply.")


class ClaudeRoutineBlock(Block):
    wakes_agents = True
    manifest = Manifest(
        id="claude-routine",
        title="Claude routines",
        kind="sink",
        level_out="L1",
        verbs={"send": "fire"},
        targets=(TargetKind("routine", slug_from="name"),),
        secrets=("<routines.*.token_env>",),
        network=("api.anthropic.com",),
        config_schema={"type": "object", "properties": {
            "routines": {"type": "object", "additionalProperties": {"type": "object", "properties": {
                "routine_id": {"type": "string"}, "token_env": {"type": "string"}, "agent": {"type": "string"},
                "name": {"type": "string"}, "slugs": {"type": "array"}}}},
            "allow_from": {"enum": ["owner", "known", "anyone"]}}},
        instructions=INSTRUCTIONS,
    )

    def __init__(self, core: "Switchboard", config: dict[str, Any]):
        super().__init__(core, config)
        self.routines: dict[str, dict[str, Any]] = {}
        for name, raw in (config.get("routines") or {}).items():
            raw = raw or {}
            rid = str(raw.get("routine_id") or "")
            if rid and not ROUTINE_RE.fullmatch(rid):
                raise BlockError(f"blocks.{self.name}.routines.{name}.routine_id must look like trig_...")
            self.routines[str(name).lower()] = {
                "routine_id": rid,
                "token_env": str(raw.get("token_env") or f"CLAUDE_ROUTINE_TOKEN_{str(name).upper().replace('-', '_')}"),
                "agent": str(raw.get("agent") or "claude").lower(),
                "name": str(raw.get("name") or f"{name} (Claude routine)"),
                "slugs": [str(s).lower() for s in (raw.get("slugs") or [name])],
            }
        self.http = httpx.AsyncClient(transport=config.get("transport"), timeout=30)

    def token(self, routine: dict[str, Any]) -> str | None:
        return self.core.cfg.env.get(routine["token_env"]) or None

    def problems(self) -> list[str]:
        out = []
        for name, r in self.routines.items():
            if not r["routine_id"]:
                out.append(f"routine {name}: no routine_id")
            if not self.token(r):
                out.append(f"routine {name}: {r['token_env']} is not set; messages stay in the {r['agent']} mailbox")
        return out

    def actions(self) -> list[Action]:
        return [Action("fire", "Message a Claude routine", "Leave the message in the routine's mailbox and start a session that replies.",
                       self.fire, capped=True, burst=True)]

    async def targets(self) -> list[dict[str, Any]]:
        return [{"kind": "routine", "id": name, "name": r["name"], "slugs": r["slugs"]} for name, r in self.routines.items()]

    def page_rows(self) -> list[tuple[str, str, str, bool | None]]:
        return [(f"routine {name}", r["routine_id"] or "no routine_id", "token set" if self.token(r) else f"{r['token_env']} missing",
                 bool(r["routine_id"] and self.token(r))) for name, r in self.routines.items()]

    async def fire(self, call: Call, *, task: str, target: str | None = None, project: str | None = None, title: str | None = None,
                   **_: Any) -> dict[str, Any]:
        name = (target or "").partition(":")[2] or target or (next(iter(self.routines)) if len(self.routines) == 1 else "")
        routine = self.routines.get(name.lower())
        if routine is None:
            raise BlockError(f"no routine {name!r}; routines: {', '.join(self.routines) or 'none'}")
        out = await self.core.agent_send(call.principal, to=routine["agent"], text=task, project=project)
        handle = out["handle"]
        call.note(target=f"routine:{name}", ref=handle, title=title, excerpt=task)
        token = self.token(routine)
        if not token or not routine["routine_id"]:
            return {**out, "fired": False, "note": f"no token for routine {name}: left in the {routine['agent']} mailbox"}
        sender = self.core.agents.label(call.principal.source)
        text = task[:MAX_TEXT] + reply_hint(handle, sender)
        try:
            resp = await self.http.post(FIRE_URL.format(routine_id=routine["routine_id"]), json={"text": text},
                                        headers={"Authorization": f"Bearer {token}", "anthropic-version": API_VERSION})
        except httpx.HTTPError as e:
            log.warning("routine %s: fire failed: %s", name, type(e).__name__)
            return {**out, "fired": False, "note": f"routine {name} didn't start ({type(e).__name__}); the message is in the mailbox"}
        if resp.status_code >= 300:
            log.warning("routine %s: fire HTTP %d", name, resp.status_code)
            return {**out, "fired": False, "note": f"routine {name} didn't start (HTTP {resp.status_code}); the message is in the mailbox"}
        body = resp.json()
        session = {"session_id": body.get("claude_code_session_id"), "session_url": body.get("claude_code_session_url"), "routine": name}
        self.core.inbox.update_handle(handle, state={"routine": session})
        return {**out, "fired": True, **session,
                "note": "the session answers with reply; it arrives in your inbox with in_reply_to set to this conversation"}

    async def aclose(self) -> None:
        await self.http.aclose()
