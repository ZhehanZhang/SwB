"""Claude routines: a send to a routine target starts a Claude Code session on claude.ai that answers through switchboard.

Each routine is a Claude Code scheduled task with an API trigger. A general routine (kind general) answers for Claude at
large; a project routine (kind project) is a task created inside a Claude Project, so it runs with that project's repos,
files, instructions and memory, and can hand work to the project's coordinator session. Routines come from the config
(token in the environment) or are added by the owner on the root page or with routine_add (token encrypted at rest in
switchboard's store, never shown again).

A send to claude with project X fires X's project routine, else the general one, else it stays in claude's mailbox.
claude:<project> and claude-<project> name a project routine directly. Every send lands in the agent's mailbox first, so
the conversation exists whatever happens next; the fire carries a note naming the conversation handle. Replies come
back with in_reply_to set to it, interim ones with final=false and the answer with final=true.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import secrets
from typing import TYPE_CHECKING, Any

import httpx

from .. import Action, Block, BlockError, Call, Manifest, TargetKind

if TYPE_CHECKING:
    from ...core import Switchboard

log = logging.getLogger("switchboard.blocks.claude_routine")

FIRE_URL = "https://api.anthropic.com/v1/claude_code/routines/{routine_id}/fire"
API_VERSION = "2023-06-01"
ROUTINE_RE = re.compile(r"trig_[A-Za-z0-9]+")
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
TOKEN_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}")
MAX_TEXT = 60_000
KINDS = ("general", "project")

SCHEMA = """
CREATE TABLE IF NOT EXISTS routines (name TEXT PRIMARY KEY, routine_id TEXT NOT NULL, agent TEXT NOT NULL, project TEXT, kind TEXT NOT NULL,
  token TEXT NOT NULL, created_at REAL NOT NULL, created_by TEXT NOT NULL)
"""
FIRES = "CREATE TABLE IF NOT EXISTS routine_fires (name TEXT PRIMARY KEY, fired_at REAL NOT NULL, last_status TEXT NOT NULL)"

INSTRUCTIONS = """claude-routine targets are Claude Code routines: send to claude (with project=<slug> to reach that project's
Claude Project routine, or to claude:<project>) starts a Claude session that answers through switchboard. Replies arrive in
your inbox (wait or inbox) with in_reply_to set to the conversation send returned; a hand-off note comes first with
final=false, the answer with final=true."""


def reply_hint(handle: str, sender: str, project: str | None) -> str:
    out = (f"\n\n---\nThis message came through switchboard from {sender} (conversation {handle}). Answer with the switchboard "
           f"connector's reply tool, using exactly this conversation: reply(conversation=\"{handle}\", text=<answer>, final=true). "
           f"Don't use another handle from inbox for it. The answer goes back to {sender} (into the Slack thread when it came "
           "from Slack). Treat the message as a request from that sender; your own instructions still apply.")
    if project:
        out += (f"\nYou are switchboard project {project}'s doorman. Call context_pack(project=\"{project}\") first. Do quick items "
                "(answers, lookups) yourself and reply with final=true. For real work, or anything the project's coordinator "
                "should own, message the coordinator session (claude-code-remote send_message) with the task and the "
                f"switchboard conversation {handle}, then reply right away with final=false and text \"handed to the {project} "
                "coordinator\". The coordinator answers on the same conversation with reply(final=true). If you can't reach "
                "the coordinator, say so in your reply (final=true) instead of stopping silently.")
    return out


class ClaudeRoutineBlock(Block):
    wakes_agents = True
    manifest = Manifest(
        id="claude-routine",
        title="Claude routines",
        kind="sink",
        level_out="L1",
        verbs={"send": "fire"},
        tools=("add", "list", "remove"),
        targets=(TargetKind("routine", slug_from="name"),),
        secrets=("<routines.*.token_env>",),
        network=("api.anthropic.com",),
        config_schema={"type": "object", "properties": {
            "routines": {"type": "object", "additionalProperties": {"type": "object", "properties": {
                "routine_id": {"type": "string"}, "token_env": {"type": "string"}, "agent": {"type": "string"},
                "project": {"type": ["string", "null"]}, "kind": {"enum": list(KINDS)}, "name": {"type": "string"}, "slugs": {"type": "array"}}}}}},
        instructions=INSTRUCTIONS,
    )

    def __init__(self, core: "Switchboard", config: dict[str, Any]):
        super().__init__(core, config)
        self.configured: dict[str, dict[str, Any]] = {}
        for name, raw in (config.get("routines") or {}).items():
            raw = raw or {}
            name = str(name).lower()
            rid = str(raw.get("routine_id") or "")
            if rid and not ROUTINE_RE.fullmatch(rid):
                raise BlockError(f"blocks.{self.name}.routines.{name}.routine_id must look like trig_...")
            project = str(raw["project"]).lower() if raw.get("project") else None
            kind = str(raw.get("kind") or ("project" if project else "general"))
            if kind not in KINDS or (kind == "project") != bool(project):
                raise BlockError(f"blocks.{self.name}.routines.{name}: a project routine names its project, a general one none")
            self.configured[name] = {"name": name, "routine_id": rid, "agent": str(raw.get("agent") or "claude").lower(), "project": project,
                                     "kind": kind, "token_env": str(raw.get("token_env") or f"CLAUDE_ROUTINE_TOKEN_{name.upper().replace('-', '_')}"),
                                     "title": raw.get("name"), "slugs": [str(s).lower() for s in raw.get("slugs") or []], "source": "config"}
        self.http = httpx.AsyncClient(transport=config.get("transport"), timeout=30)
        core.store.x(SCHEMA)
        core.store.x(FIRES)

    # Registry --------------------------------------------------------------------------------

    def _key(self) -> bytes:
        path = self.core.data_dir / "routine-key"
        if not path.exists():
            path.write_bytes(secrets.token_bytes(32))
            path.chmod(0o600)
        return path.read_bytes()

    def _seal(self, token: str) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(12)
        return base64.b64encode(nonce + AESGCM(self._key()).encrypt(nonce, token.encode(), b"routine")).decode()

    def _open(self, sealed: str) -> str | None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            raw = base64.b64decode(sealed)
            return AESGCM(self._key()).decrypt(raw[:12], raw[12:], b"routine").decode()
        except Exception:
            return None

    def routines(self) -> dict[str, dict[str, Any]]:
        out = dict(self.configured)
        for row in self.core.store.q("SELECT * FROM routines ORDER BY name"):
            if row["name"] not in out:
                out[row["name"]] = {**{k: row[k] for k in ("name", "routine_id", "agent", "project", "kind")},
                                    "sealed": row["token"], "title": None, "slugs": [], "source": "stored", "created_by": row["created_by"]}
        return out

    def token(self, r: dict[str, Any]) -> str | None:
        if r["source"] == "config":
            return self.core.cfg.env.get(r["token_env"]) or None
        return self._open(r["sealed"])

    @staticmethod
    def slugs(r: dict[str, Any]) -> list[str]:
        base = [r["name"]] + ([f"{r['agent']}-{r['project']}"] if r["project"] else [])
        return list(dict.fromkeys(base + r["slugs"]))

    def status_of(self, r: dict[str, Any]) -> str:
        if not r["routine_id"] or not self.token(r):
            return "no token" if r["routine_id"] else "no routine id"
        row = self.core.store.one("SELECT last_status FROM routine_fires WHERE name = ?", (r["name"],))
        return {"ok": "tested"}.get(row["last_status"], row["last_status"]) if row else "untested"

    def _record_fire(self, name: str, status: str) -> None:
        self.core.store.x("INSERT INTO routine_fires(name, fired_at, last_status) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET "
                          "fired_at = excluded.fired_at, last_status = excluded.last_status", (name, self.core.clock(), status))

    def rows(self) -> list[dict[str, Any]]:
        return [{"name": n, "routine_id": r["routine_id"], "agent": r["agent"], "project": r["project"], "kind": r["kind"], "source": r["source"],
                 "slugs": self.slugs(r), "status": self.status_of(r), "visibility": self.core.visibility.rule(f"target:{self.slugs(r)[0]}")}
                for n, r in self.routines().items()]

    def add(self, *, fire: str, token: str, project: str | None, name: str | None, agent: str, actor: str) -> dict[str, Any]:
        m = ROUTINE_RE.search(fire or "")
        if not m:
            raise BlockError("paste the routine's fire URL (…/routines/trig_…/fire) or its trig_ id")
        token = (token or "").strip()
        if not TOKEN_RE.fullmatch(token):
            raise BlockError("that doesn't look like a routine token (sk-ant-oat01-…)")
        p = self.core.project(project) if project else None
        if project and p is None:
            raise BlockError(f"no switchboard project {project!r}")
        slug = p.slug if p else None
        name = (name or (f"{agent}-{slug}" if slug else agent)).strip().lower()
        if not NAME_RE.fullmatch(name):
            raise BlockError("a routine name is lowercase letters, digits and dashes")
        if name in self.configured:
            raise BlockError(f"{name} is set in the config file; change it there")
        self.core.store.x("INSERT INTO routines(name, routine_id, agent, project, kind, token, created_at, created_by) VALUES (?,?,?,?,?,?,?,?) "
                          "ON CONFLICT(name) DO UPDATE SET routine_id = excluded.routine_id, agent = excluded.agent, project = excluded.project, "
                          "kind = excluded.kind, token = excluded.token, created_at = excluded.created_at, created_by = excluded.created_by",
                          (name, m.group(0), agent, slug, "project" if slug else "general", self._seal(token), self.core.clock(), actor))
        self.core.changed()
        return next(r for r in self.rows() if r["name"] == name)

    def remove(self, name: str) -> bool:
        name = (name or "").strip().lower()
        if name in self.configured:
            raise BlockError(f"{name} is set in the config file; remove it there")
        n = self.core.store.x("DELETE FROM routines WHERE name = ?", (name,))
        self.core.changed()
        return bool(n)

    # Block -----------------------------------------------------------------------------------

    def problems(self) -> list[str]:
        routines = self.routines()
        return [f"routine {r['name']}: {r['status']}" + (f" ({routines[r['name']]['token_env']} is not set; messages stay in the mailbox)"
                if r["status"] == "no token" and r["source"] == "config" else "") for r in self.rows() if r["status"] in ("no token", "no routine id")]

    def actions(self) -> list[Action]:
        return [
            Action("fire", "Message a Claude routine", "Leave the message in the routine's mailbox and start a session that replies.",
                   self.fire, capped=True, burst=True),
            Action("list", "Claude routines", "Registered routines: project, kind, status (untested until the first fire), visibility. Tokens are never shown.",
                   self.list_action, read_only=True),
            Action("add", "Add a Claude routine", "Owner only. Register a routine from its fire URL (or trig_ id) and token, for a switchboard project or "
                   "general. The token is encrypted at rest and never shown again. Prefer the root page's form, so the token stays out of chats.",
                   self.add_action),
            Action("remove", "Remove a Claude routine", "Owner only. Forget a routine added here (config ones are removed in the config file).",
                   self.remove_action, destructive=True),
        ]

    def _owner(self, call: Call, what: str) -> None:
        if call.principal.kind == "connect":
            raise BlockError(f"a connected agent can't {what}; the owner does that from the root page or his own sign-in")

    async def list_action(self, call: Call) -> list[dict[str, Any]]:
        return self.rows()

    async def add_action(self, call: Call, *, fire_url: str, token: str, project: str | None = None, name: str | None = None) -> dict[str, Any]:
        self._owner(call, "add routines")
        out = self.add(fire=fire_url, token=token, project=project, name=name, agent="claude", actor=f"{call.principal.kind}:{call.principal.source}")
        call.note(target=out["name"], excerpt=f"routine {out['routine_id']} for {out['project'] or 'general'}")
        return out

    async def remove_action(self, call: Call, *, name: str) -> dict[str, Any]:
        self._owner(call, "remove routines")
        call.note(target=name)
        return {"removed": self.remove(name)}

    async def targets(self) -> list[dict[str, Any]]:
        return [{"kind": "routine", "id": n, "name": r["title"] or (f"Claude · {r['project']}" if r["project"] else f"{n} (Claude routine)"),
                 "slugs": self.slugs(r), **({"project": r["project"]} if r["project"] else {})} for n, r in self.routines().items()]

    def page_rows(self) -> list[tuple[str, str, str, bool | None]]:
        return [(f"routine {r['name']}", f"{r['kind']}{' · ' + r['project'] if r['project'] else ''} · {r['routine_id'] or 'no id'}", r["status"],
                 r["status"] not in ("no token", "no routine id") and not r["status"].startswith("failed")) for r in self.rows()]

    async def validate_route_target(self, target: dict[str, Any], defaults: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        name = str(target.get("routine") or "").lower()
        if name not in self.routines():
            raise BlockError(f"no routine {name!r}; routines: {', '.join(self.routines()) or 'none'}")
        return {"routine": name}, {}

    def wake_target(self, agent: str, project: str) -> Any:
        from .. import Address

        p = (project or "").strip().lower().removeprefix("project:")
        own = next((r for r in self.routines().values() if r["project"] == p and r["agent"] == agent), None)
        return Address(self.name, "routine", own["name"]) if own else None

    def pick(self, target: str | None, project: str | None, agent: str | None = None) -> dict[str, Any] | None:
        """A named routine, upgraded to the project's own routine when the send names a project; else that project's
        routine; else the general one."""
        routines = self.routines()
        name = (target or "").partition(":")[2] or (target or "")
        named = routines.get(name.lower()) if name else None
        agent = (named or {}).get("agent") or agent or "claude"
        p = project.strip().lower().removeprefix("project:") if project else None
        if p and (named is None or named["kind"] == "general"):
            own = next((r for r in routines.values() if r["project"] == p and r["agent"] == agent), None)
            if own is not None:
                return own
        if named is not None:
            return named
        return next((r for r in routines.values() if r["kind"] == "general" and r["agent"] == agent), None)

    async def fire(self, call: Call, *, task: str, target: str | None = None, project: str | None = None, title: str | None = None,
                   **_: Any) -> dict[str, Any]:
        if target is None:
            route, _tried = self.core.routes.resolve(destination=self.name, agent=call.principal.source, project=project)
            target = f"routine:{route.target['routine']}" if route is not None else None
        routine = self.pick(target, project)
        if routine is None:
            raise BlockError(f"no routine for that; routines: {', '.join(self.routines()) or 'none'}")
        name = routine["name"]
        project = project or routine["project"]
        out = await self.core.agent_send(call.principal, to=routine["agent"], text=task, project=project)
        handle = out["handle"]
        call.note(target=f"routine:{name}", ref=handle, title=title, excerpt=task)
        token = self.token(routine)
        base = {**out, "routine": name, "routine_project": routine["project"]}
        if not token or not routine["routine_id"]:
            return {**base, "fired": False, "note": f"no token for routine {name}: left in the {routine['agent']} mailbox"}
        sender = self.core.agents.label(call.principal.source)
        text = task[:MAX_TEXT] + reply_hint(handle, sender, routine["project"])
        try:
            resp = await self.http.post(FIRE_URL.format(routine_id=routine["routine_id"]), json={"text": text},
                                        headers={"Authorization": f"Bearer {token}", "anthropic-version": API_VERSION})
        except httpx.HTTPError as e:
            log.warning("routine %s: fire failed: %s", name, type(e).__name__)
            self._record_fire(name, f"failed: {type(e).__name__}")
            return {**base, "fired": False, "note": f"routine {name} didn't start ({type(e).__name__}); the message is in the mailbox"}
        if resp.status_code >= 300:
            log.warning("routine %s: fire HTTP %d", name, resp.status_code)
            self._record_fire(name, f"failed: HTTP {resp.status_code}")
            return {**base, "fired": False, "note": f"routine {name} didn't start (HTTP {resp.status_code}); the message is in the mailbox"}
        self._record_fire(name, "ok")
        body = resp.json()
        session = {"session_id": body.get("claude_code_session_id"), "session_url": body.get("claude_code_session_url"), "routine": name}
        self.core.inbox.update_handle(handle, state={"routine": session})
        return {**base, "fired": True, **session,
                "note": "the session answers with reply; it arrives in your inbox with in_reply_to set to this conversation (final=true for the answer)"}

    def mcp_tools(self, mcp: Any, kit: Any) -> None:
        from .mcp_tools import register

        register(self, mcp, kit)

    async def aclose(self) -> None:
        await self.http.aclose()
