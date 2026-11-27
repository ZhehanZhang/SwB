"""The core: blocks, agents, identities, targets, conversations (the inbox), routes, projects and scoped memory, approvals and audit.

It names no vendor. Blocks bring the ways in and out; the core routes a caller's verb (send, reply, status, result,
cancel, targets) to the block that owns the target, through audit and caps.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from .audit import Audit, iso
from . import targets as tgt
from .agents import Agents
from .auth import Auth, Principal
from .blocks import Action, Address, Block, BlockError, Call
from .blocks import load as load_block
from .claims import Claims
from .config import Config, load_config
from .connect import Connections
from .inbox import Inbox
from .memory import GENERAL, Memory, MemoryError_
from .projects import Project, ProjectError, Projects
from .routes import RouteError, Routes
from .store import Store
from .visibility import Visibility

DestinationError = BlockError

log = logging.getLogger("switchboard")

PACK_LIMIT = 14_000
TARGETS_TTL_S = 60.0


class CoreError(Exception):
    pass


class Switchboard:
    def __init__(
        self,
        config_path: str | Path | None,
        data_dir: str | Path,
        env: dict[str, str] | None = None,
        clock: Callable[[], float] = time.time,
        http_transport: httpx.AsyncBaseTransport | None = None,
        oauth_http: httpx.AsyncClient | None = None,
        access_verifier: Any = None,
        destination_options: dict[str, dict[str, Any]] | None = None,
        slack_options: dict[str, Any] | None = None,
        block_options: dict[str, dict[str, Any]] | None = None,
    ):
        self.cfg: Config = load_config(config_path, env)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.http_transport = http_transport
        legacy = self.data_dir / "capy-bridge.db"
        db = self.data_dir / "switchboard.db"
        if legacy.exists() and not db.exists():
            for suffix in ("", "-wal", "-shm"):
                if Path(str(legacy) + suffix).exists():
                    Path(str(legacy) + suffix).rename(str(db) + suffix)
        self.store = Store(db, clock=clock)
        self.audit = Audit(self.store, self.cfg.timezone)
        self.projects = Projects(self.store)
        self.memory = Memory(self.store, self.projects, self.cfg.mirror_dir, self.cfg.mirror_debounce_s)
        self.routes = Routes(self.store)
        self.inbox = Inbox(self.store)
        types = {name: load_block(str(sec.get("use") or name)) for name, sec in self.cfg.blocks.items()}
        self.agents = Agents.build(self.cfg.agents, [a for t in types.values() for a in t.manifest.agents])
        self.projects.link_keys = tuple(dict.fromkeys([*self.agents.link_keys(), *(k for t in types.values() for k in t.manifest.link_keys)]))
        self.connections = Connections(self.store)
        self.claims = Claims(self.store)
        self.store.x("CREATE TABLE IF NOT EXISTS conversation_turns (root TEXT PRIMARY KEY, turns INTEGER NOT NULL, paused_at REAL)")
        self.visibility = Visibility(self.cfg.visibility, self.store)
        self.auth = Auth(self.cfg, self.store, http=oauth_http, access_verifier=access_verifier, secret=self._secret(), agents=self.agents,
                         connections=self.connections)
        self.auth.audit = self.audit
        self._tasks: set[asyncio.Task[Any]] = set()
        self.change_listeners: list[Any] = []
        self.slack_options = dict(slack_options or {})
        self.slack_connect = bool(self.slack_options.pop("connect", True))
        self._targets: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self.target_errors: dict[str, str] = {}
        opts = {**(destination_options or {}), **(block_options or {})}
        self.blocks: dict[str, Block] = {}
        for name, sec in self.cfg.blocks.items():
            self.blocks[name] = types[name](self, {**sec, "_name": name, **opts.get(name, {})})
        for b in self.blocks.values():
            b.bind()
        self._seed_routes(self.cfg.routes)

    @property
    def origins(self) -> dict[str, Block]:
        """Blocks that take callers in (they serve routes and authenticate)."""
        return {n: b for n, b in self.blocks.items() if b.has_in}

    @property
    def destinations(self) -> dict[str, Block]:
        """Blocks that reach a system out (they declare actions)."""
        return {n: b for n, b in self.blocks.items() if b.has_out}

    @property
    def slack(self) -> Any:
        """The Slack engine of the slack block, if one is configured (admin page, avatars, approvals)."""
        for b in self.blocks.values():
            engine = getattr(b, "engine", None)
            if engine is not None:
                return engine
        return None

    def _seed_routes(self, seeds: list[dict[str, Any]]) -> None:
        """The config's routes: seed each agent/project slot once (routes_set and the page manage them after). Empty by default."""
        for seed in seeds:
            match, to = dict(seed.get("match") or {}), str(seed.get("to") or "")
            addr = tgt.parse(to, self.blocks)
            block = self.blocks.get(addr.block) if addr else None
            if block is None or not block.supports_routes or not addr.kind:
                raise CoreError(f"routes: {to!r} is not <block>:<kind>:<id> for a block that takes routes")
            agent, project = match.get("agent") or None, match.get("project") or None
            if self.store.one("SELECT 1 FROM routes WHERE destination = ? AND agent = ? AND project = ?", (block.name, agent or "", project or "")):
                continue
            self.routes.set(destination=block.name, agent=agent, project=project, target={addr.kind: addr.id}, defaults=dict(seed.get("defaults") or {}),
                            allow_override=bool(seed.get("allow_override", True)), note="seeded from routes: in the config", by="config")

    def _secret(self) -> bytes:
        path = self.data_dir / "form-secret"
        if not path.exists():
            path.write_bytes(secrets.token_bytes(32))
            path.chmod(0o600)
        return path.read_bytes()

    def secret(self, name: str) -> str | None:
        return self.cfg.env.get(name) or self.claims.secret(name)

    @property
    def base_url(self) -> str:
        return self.cfg.public_url or f"http://localhost:{self.cfg.env.get('PORT', '8000')}"

    # Lifecycle ------------------------------------------------------------------------

    def spawn(self, coro: Awaitable[Any], name: str = "") -> asyncio.Task[Any]:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def start(self) -> None:
        for b in self.blocks.values():
            await b.start()

    async def aclose(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        for b in reversed(list(self.blocks.values())):
            await b.aclose()
        await self.auth.http.aclose()
        if self.memory.mirror_pending:
            self.memory.flush_mirror()
        self.store.close()

    def status(self) -> dict[str, Any]:
        problems = list(self.cfg.problems())
        next_steps = {n: b.next_step() for n, b in self.blocks.items()}
        for b in self.blocks.values():
            problems += b.problems()
        return {
            "status": "degraded" if problems else "ok",
            "problems": problems,
            "next_steps": [step for step in next_steps.values() if step],
            "oauth": bool(self.cfg.issuer and self.cfg.owner_sign_in),
            "blocks": {n: {**(b.status() if not b.has_out else {}), "use": b.manifest.id, "kind": b.manifest.kind, "level": {"out": b.manifest.level_out, "in": b.manifest.level_in},
                           "online": b.online, "next_step": next_steps[n]} for n, b in self.blocks.items()},
            "origins": sorted(self.origins),
            "destinations": {n: {"online": d.online, **d.status()} for n, d in self.destinations.items()},
            "static_keys": [k.name for k in self.cfg.static_keys],
            "mirror": bool(self.cfg.mirror_dir),
            "turn_cap": {"per_conversation": self.cfg.turn_cap,
                         "paused": self.store.one("SELECT COUNT(*) AS n FROM conversation_turns WHERE paused_at IS NOT NULL")["n"]},
        }

    health = status

    # Routing an action to a destination -----------------------------------------------------

    def known_agents(self) -> list[str]:
        """Agent names a message can be addressed to: OAuth agents, bearer-key names, and agents named in routes."""
        names = set(self.agents.ids()) | {k.name for k in self.cfg.static_keys if k.name not in self.cfg.inbox_keys} | set(self.cfg.source_names.values())
        names |= set(self.cfg.inbox_keys.values())
        names |= {r["agent"] for r in self.store.q("SELECT DISTINCT agent FROM routes WHERE agent IS NOT NULL AND agent != ''")}
        names |= set(self.connections.agents())
        return sorted(names)

    async def agent_send(self, principal: Principal, *, to: str, text: str, project: str | None = None, in_reply_to: str | None = None,
                         final: bool = True, allow_self: bool = False, handle: str | None = None,
                         handle_meta: dict[str, Any] | None = None, _reply: bool = False) -> dict[str, Any]:
        """Put a message in another agent's mailbox. The sender is the authenticated caller, never anything in the text.
        A reply (in_reply_to) goes back to whoever sent the original, which may be a block's own agent such as slack.
        allow_self opens a conversation with another session of the same agent (a Claude Project testing its own routine)."""
        to = (to or "").strip().lower()
        original = self.inbox.handle(in_reply_to) if in_reply_to else None
        root = (original["meta"].get("root") or original["handle"]) if original is not None else None
        if original is not None and self._listen_conversation(original) and not (
            _reply and original["meta"].get("listen") and original["owner"] == principal.source and original["ref"] == to
        ):
            raise CoreError("listen conversations can only be answered with reply")
        if to not in self.known_agents() and not (original is not None and original["ref"] == to):
            raise CoreError(f"no agent {to!r}; known agents: {', '.join(self.known_agents())}")
        own = original is not None and original["ref"] == to and original["owner"] == to
        if to == principal.source and not (allow_self or own):
            raise CoreError("that is you; agent_send goes to another agent")
        text = (text or "").strip()
        if not text:
            raise CoreError("text is empty")
        p = self.project(project)
        if root is not None:
            self.turn(root, principal)
        sender = {"kind": "agent", "agent": principal.source, "label": self.agents.label(principal.source),
                  "auth": principal.kind, "client_id": principal.client_id, "origin": principal.origin,
                  "trust": principal.trust}
        meta = {**(handle_meta or {}), "from": sender, **({"in_reply_to": in_reply_to, "final": final, "root": root} if in_reply_to else {})}
        handle = self.inbox.open_handle(handle=handle, owner=to, destination="mailbox", ref=principal.source, project=p.slug if p else None, title=None,
                                        meta=meta, status="delivered", target=f"agent:{to}")
        self.inbox.update_handle(handle, open=False)
        await self.inbox.add(handle, "message", {"text": text[:20_000], "project": p.slug if p else None, "from": sender,
                                                 **({"in_reply_to": in_reply_to, "final": final} if in_reply_to else {})})
        self.audit.record(origin=principal.origin, source=principal.source, client=principal.client_id, destination="mailbox", action="agent_send",
                          outcome="ok", target=to, ref=handle, excerpt=text)
        return {"to": to, "handle": handle, "conversation": handle, "delivered": True, "mailbox": True, "project": p.slug if p else None}

    def turn(self, root: str, principal: Principal) -> None:
        """Count one more turn on a mailbox conversation: a send's handle plus every forward and reply (a new send starts
        at zero). At turn_cap that one conversation pauses, as a likely loop, and the owner gets a Slack DM. Slack and
        Capy threads aren't counted here: their handles are shared per workspace or Captain, and Slack has its own
        per-thread bot post guard."""
        cap = self.cfg.turn_cap
        if not cap:
            return
        row = self.store.one("SELECT turns, paused_at FROM conversation_turns WHERE root = ?", (root,))
        turns = (row["turns"] if row else 1) + 1
        if row is not None and row["paused_at"] is None and turns <= cap:
            self.store.x("UPDATE conversation_turns SET turns = ? WHERE root = ?", (turns, root))
            return
        if row is None:
            self.store.x("INSERT INTO conversation_turns(root, turns) VALUES (?, ?)", (root, turns))
            if turns <= cap:
                return
        stop = f"conversation {root} is paused: it reached {cap} turns, which looks like a loop. Start a new send if the work goes on."
        if row is not None and row["paused_at"] is not None:
            raise CoreError(stop)
        self.store.x("UPDATE conversation_turns SET paused_at = ? WHERE root = ?", (self.clock(), root))
        self.audit.record(origin=principal.origin, source=principal.source, client=principal.client_id, destination="core", action="turn_cap",
                          outcome="refused", ref=root, detail=f"paused at {cap} turns")
        engine = self.slack
        if engine is not None:
            task = asyncio.ensure_future(engine.dm_owner(f"switchboard paused conversation {root} after {cap} turns (last from "
                                                         f"{principal.source}); it looks like a loop. Other conversations are unaffected."))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        raise CoreError(stop)

    def destination(self, name: str) -> Block:
        d = self.blocks.get(name)
        if d is None:
            raise CoreError(f"block {name!r} is not configured; configured: {', '.join(self.blocks) or 'none'}")
        return d

    block = destination

    def bind_listener(self, fn: Any) -> None:
        self.change_listeners.append(fn)

    async def deliver(self, conversation: str, kind: str, data: dict[str, Any]) -> int:
        """A block reports what it heard back on a conversation: the item lands in its owner's inbox."""
        return await self.inbox.add(conversation, kind, data)

    def alias_used(self, alias: str, agent: str) -> None:
        """A deprecated tool name was called: counted per agent, so the aliases can go once nobody uses them."""
        log.info("deprecated alias %s called by %s", alias, agent)
        self.store.x("INSERT INTO alias_use(alias, agent, count, last_at) VALUES (?,?,1,?) ON CONFLICT(alias, agent) DO UPDATE SET count = count + 1, last_at = excluded.last_at",
                     (alias, agent, self.clock()))

    def alias_counts(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.store.q("SELECT alias, agent, count, last_at FROM alias_use ORDER BY count DESC")]

    # Targets ------------------------------------------------------------------------------------

    async def targets(self, *, block: str | None = None, kind: str | None = None, query: str | None = None, refresh: bool = False) -> list[dict[str, Any]]:
        """Every block's targets, plus an agent:<name> mailbox per known agent and a project:<slug> route per project.
        A block that fails keeps its last good list, and the error shows in target_errors."""
        out: list[dict[str, Any]] = []
        now = self.clock()
        for name, b in self.blocks.items():
            if type(b).targets is Block.targets or (block and block != name):
                continue
            at, got = self._targets.get(name, (0.0, []))
            if refresh or now - at > TARGETS_TTL_S or not got:
                try:
                    got = await b.targets()
                    self._targets[name] = (now, got)
                    self.target_errors.pop(name, None)
                except Exception as exc:
                    self.target_errors[name] = f"{type(exc).__name__}: {exc}"[:300]
            for t in got:
                out.append({**t, "address": str(Address(name, t["kind"], t["id"])), "block": name, "level": b.manifest.level_out,
                            "slugs": [s for s in t.get("slugs") or [] if s]})
        if not block or block == "agent":
            for a in self.known_agents():
                if a not in tgt.NOT_TARGETS:
                    out.append({"address": f"agent:{a}", "block": "agent", "kind": "agent", "id": a, "name": self.agents.label(a), "slugs": [a],
                                "level": "L0", "persona": {"id": a}})
        if not block or block == "project":
            for p in self.projects.list():
                out.append({"address": f"project:{p.slug}", "block": "project", "kind": "project", "id": p.slug, "name": p.name, "slugs": [p.slug],
                            "level": None, "routed_to": (str(a) if (a := self.route_address(None, p.slug)) else None)})
        if kind:
            out = [t for t in out if t["kind"] == kind]
        if query:
            q = query.strip().lower()
            out = [t for t in out if q in t["address"].lower() or q in str(t.get("name") or "").lower() or any(q in s for s in t["slugs"])]
        prio = tuple(self.cfg.targets.get("priority") or ())
        return sorted(out, key=lambda t: (tgt.rank(t, prio), t["address"]))

    def slug_map(self, targets: list[dict[str, Any]], aliases: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
        return dict(tgt.by_slug(targets, self.cfg.targets.get("priority") or (), {**(self.cfg.targets.get("aliases") or {}), **(aliases or {})}))

    def route_address(self, agent: str | None, project: str | None) -> Address | None:
        """Where a route sends this agent's work for this project, as an address (None: no route)."""
        for name, b in self.blocks.items():
            if not b.supports_routes:
                continue
            r, _ = self.routes.resolve(destination=name, agent=agent or "", project=project)
            if r is not None:
                return b.route_address(r.target)
        return None

    async def resolve_to(self, to: str) -> Address:
        """A send's to: an address, a legacy captain:/project:/thread: target, an agent name, a block name or a slug."""
        text = (to or "").strip()
        addr = tgt.parse(text, self.blocks)
        if addr is not None:
            if addr.block == "project" and not self._is_project(addr.id):
                owner = self._kind_owner("project")
                if owner:
                    return Address(owner, "project", addr.id)
                raise CoreError(f"no project {addr.id!r}")
            return addr
        head, sep, rest = text.partition(":")
        if sep and head.lower() in self.known_agents():
            if self._is_project(rest.strip().lower()):
                addr = next((a for b in self.blocks.values() if b.wakes_agents and (a := b.wake_target(head.lower(), rest.strip().lower()))), None)
                if addr is not None:
                    return addr
            for name, b in self.blocks.items():
                if b.wakes_agents:
                    t = self.slug_map(await self.targets(block=name)).get(tgt.slugify(f"{head}-{rest}"))
                    if t is not None:
                        return Address(t["block"], t["kind"], t["id"])
        if sep and head.lower() in tgt.LEGACY_KINDS:
            owner = self._kind_owner(head.lower())
            if owner:
                return Address(owner, head.lower(), rest.strip())
        word = text.lower()
        if word in self.blocks:
            return self.blocks[word].default_address()
        if word in self.known_agents():
            for name, b in self.blocks.items():
                if b.wakes_agents:
                    t = self.slug_map(await self.targets(block=name)).get(word)
                    if t is not None:
                        return Address(t["block"], t["kind"], t["id"])
            return Address("agent", "agent", word)
        if word.startswith("jam_") or word.startswith("project_"):
            owner = next((n for n, b in self.blocks.items() if b.owns_ref(text)), None)
            if owner:
                return Address(owner, "", text)
        hits = self.slug_map(await self.targets())
        t = hits.get(tgt.slugify(word))
        if t is None:
            raise CoreError(f"no target {to!r}; list them with targets(), or pass an address like agent:claude or <block>:<kind>:<id>")
        return Address(t["block"], t["kind"], t["id"])

    def _is_project(self, slug: str) -> bool:
        try:
            self.projects.get(slug.strip().lower())
            return True
        except ProjectError:
            return False

    def _kind_owner(self, kind: str) -> str | None:
        owners = [n for n, b in self.blocks.items() if any(k.kind == kind for k in b.manifest.targets)]
        return owners[0] if len(owners) == 1 else None

    # Generic verbs ------------------------------------------------------------------------------

    def _listen_conversation(self, handle: dict[str, Any]) -> bool:
        meta = handle["meta"]
        root = self.inbox.handle(meta["root"]) if meta.get("root") and meta["root"] != handle["handle"] else None
        return bool(meta.get("listen") or (root and root["meta"].get("listen")))

    async def send(self, principal: Principal, *, task: str, to: str | None = None, project: str | None = None, title: str | None = None,
                   files: list[dict[str, Any]] | None = None, repos: list[str] | None = None, new_conversation: bool | None = None,
                   forward: str | None = None, handle_meta: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send work or a message. Without to, the route for the caller and project decides; agent:<x> leaves it in a mailbox."""
        if principal.targets is not None:
            await self.check_target(principal, to, project, forward)
        handle = None
        if forward is not None:
            original = self.inbox.handle(forward)
            if original is None or original["destination"] != "mailbox" or original["owner"] != principal.source:
                raise CoreError("you can only forward a conversation you received")
            if self._listen_conversation(original):
                raise CoreError("listen conversations cannot be forwarded; use reply")
            meta = original["meta"]
            sender = meta.get("from") or {}
            trust = meta.get("slack_trust") if sender.get("origin") == "slack" else sender.get("trust")
            if trust != "owner":
                raise CoreError("you can only forward an owner-trust conversation")
            if meta.get("boundary"):
                raise CoreError(f"conversation {forward} is bounded to {meta['boundary']} and cannot be forwarded")
            handle = self.inbox.new_handle()
            if not self.store.n("UPDATE handles SET meta = json_set(meta, '$.forwarded_to', ?) "
                                "WHERE handle = ? AND json_extract(meta, '$.forwarded_to') IS NULL", (handle, forward)):
                raise CoreError(f"conversation {forward} has already been forwarded")
            handle_meta = {**(handle_meta or {}), "forward_of": forward, "root": meta.get("root") or forward}
        try:
            if handle is not None:
                self.turn(handle_meta["root"], principal)
            return await self._send(principal, task=task, to=to, project=project, title=title, files=files, repos=repos,
                                    new_conversation=new_conversation, handle=handle, handle_meta=handle_meta)
        except BaseException:
            if handle is not None:
                self.store.x("UPDATE handles SET meta = json_remove(meta, '$.forwarded_to') "
                             "WHERE handle = ? AND json_extract(meta, '$.forwarded_to') = ? "
                             "AND NOT EXISTS (SELECT 1 FROM inbox WHERE handle = ?)", (forward, handle, handle))
            raise

    async def check_target(self, principal: Principal, to: str | None, project: str | None, forward: str | None = None) -> None:
        """A key limited by auth.key_targets sends only to those targets, never forwards, and names no project outside its scopes.
        The target is checked as it will be delivered, after a project picks a different routine or wake target."""
        if forward is not None:
            raise CoreError(f"the {principal.source} key can't forward")
        if not to:
            raise CoreError(f"the {principal.source} key must send to one of: {', '.join(principal.targets or ()) or 'nothing'}")
        allowed = set()
        for t in principal.targets or ():
            with contextlib.suppress(CoreError):
                allowed.add(str(await self.resolve_to(t)))
        addr = await self.resolve_to(to)
        if not addr.id:
            raise CoreError(f"the {principal.source} key must name one target, not a whole block; it may send to {', '.join(principal.targets or ())}")
        if addr.block == "agent" and project and not to.strip().lower().startswith("agent:"):
            addr = next((a for b in self.blocks.values() if b.wakes_agents and (a := b.wake_target(addr.id, project))), addr)
        block = self.blocks.get(addr.block)
        if block is not None:
            addr = block.effective_target(addr, project)
        if str(addr) not in allowed:
            raise CoreError(f"the {principal.source} key may send only to {', '.join(principal.targets or ()) or 'nothing'}")
        if project and not principal.allows("project:" + project.strip().lower().removeprefix("project:")):
            raise CoreError(f"the {principal.source} key can't send in project {project}")

    async def _send(self, principal: Principal, *, task: str, to: str | None, project: str | None, title: str | None,
                    files: list[dict[str, Any]] | None, repos: list[str] | None, new_conversation: bool | None,
                    handle: str | None, handle_meta: dict[str, Any] | None) -> dict[str, Any]:
        addr = await self.resolve_to(to) if to else None
        if addr is not None and addr.block == "agent" and project and not to.strip().lower().startswith("agent:"):
            addr = next((a for b in self.blocks.values() if b.wakes_agents and (a := b.wake_target(addr.id, project))), addr)
        if addr is not None and addr.block == "agent":
            return {**await self.agent_send(principal, to=addr.id, text=task, project=project, handle=handle, handle_meta=handle_meta), "to": str(addr)}
        if addr is not None and addr.block == "project":
            project, addr = addr.id, None
        if addr is not None:
            block = self.destination(addr.block)
        else:
            block = self._routing_block(principal, project)
        action = block.verb("send")
        if action is None:
            raise CoreError(f"{block.name} takes no send; its tools are {', '.join(block.manifest.tools) or 'none'}")
        message = {"task": task, "title": title, "project": project, "repos": repos, "files": files, "new_thread": new_conversation}
        kw = block.send_args(addr if addr is not None and (addr.kind or addr.id) else None, {k: v for k, v in message.items() if v is not None})
        out = await self.invoke(principal, block.name, action, kw, handle=handle, handle_meta=handle_meta)
        if isinstance(out, dict):
            out.setdefault("conversation", out.get("handle"))
            out.setdefault("block", block.name)
        return out

    def _routing_block(self, principal: Principal, project: str | None) -> Block:
        routable = [b for b in self.blocks.values() if b.supports_routes and b.verb("send") is not None]
        p = project.strip().lower().removeprefix("project:") if project else None
        levels = ("agent+project", "project", "agent", "global")
        found = [(levels.index(r.level), i, b) for i, b in enumerate(routable) if (r := self.routes.resolve(destination=b.name, agent=principal.source, project=p)[0])]
        if found:
            return min(found, key=lambda x: (x[0], x[1]))[2]
        if routable:
            return routable[0]
        raise CoreError("nothing to send to: no block that takes routes is configured; pass to (an agent:<name> mailbox or a block target)")

    def project_systems(self, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Per switchboard project, its counterpart in each system: the target each route reaches, block targets that
        belong to it (a Claude project routine), and its links (capy_project, claude_project, chatgpt_project, ...)."""
        names = {(t["block"], t["id"]): t.get("name") or t["id"] for t in targets}
        routes = [r.as_dict() for r in self.routes.list()]
        out = []
        for p in self.projects.list():
            systems: dict[str, str] = {}
            for r in routes:
                if r["project"] == p.slug or (not r["project"] and not r["agent"] and r["destination"] not in systems):
                    kind, ident = next(iter(r["target"].items())) if r["target"] else ("", "")
                    label = names.get((r["destination"], str(ident))) or f"{kind} {ident}"
                    systems[r["destination"]] = label + ("" if r["project"] else " (global route)")
            for t in targets:
                if t.get("project") == p.slug:
                    systems[f"{t['block']} {t['kind']}"] = t.get("name") or t["id"]
            for k, v in p.links.items():
                if k != "repos" and v:
                    systems[k] = v if isinstance(v, str) else ", ".join(map(str, v))
            out.append({"slug": p.slug, "name": p.name, "systems": systems})
        return out

    def mailbox_replies(self, handle: str, sender: str) -> list[dict[str, Any]]:
        """Replies the other side sent on a mailbox conversation, oldest first: interim (final false) and the answer."""
        out = []
        for row in self.store.q("SELECT data, at FROM inbox WHERE owner = ? AND kind = 'message' ORDER BY id", (sender,)):
            d = json.loads(row["data"])
            if d.get("in_reply_to") == handle:
                out.append({"text": d.get("text"), "final": d.get("final", True), "from": d.get("from"), "at": iso(row["at"])})
        return out

    def _conversation(self, principal: Principal, ref: str) -> tuple[Block | None, dict[str, Any] | None]:
        """The block and handle behind a conversation reference (h_ handle, or a block's own id such as a thread id)."""
        ref = (ref or "").strip()
        if ref.startswith("h_"):
            h = self.inbox.handle(ref)
            if h is None or (h["owner"] != principal.source and not (h["destination"] == "mailbox" and h["ref"] == principal.source)):
                raise CoreError(f"no conversation {ref!r}")
            return self.blocks.get(h["destination"]), h
        owner = next((b for b in self.blocks.values() if b.owns_ref(ref)), None)
        if owner is None:
            raise CoreError(f"no block owns {ref!r}; pass the conversation handle (h_...)")
        return owner, None

    async def verb(self, principal: Principal, verb: str, ref: str, *, to_owner: bool = False, **kw: Any) -> Any:
        """reply, status, result or cancel on a conversation, run by the block that owns it."""
        block, h = self._conversation(principal, ref)
        final = kw.pop("final", None)
        if to_owner and (verb != "reply" or h is None or h["destination"] != "mailbox" or not h["meta"].get("listen")):
            raise CoreError("to_owner is only allowed when replying to a listen conversation")
        if h is not None and h["destination"] == "mailbox":
            if verb == "reply":
                if h["owner"] != principal.source:
                    raise CoreError("that conversation is one you sent; replies to it come from the other side (see result)")
                if to_owner:
                    text = (kw.get("text") or "").strip()
                    if not text:
                        raise CoreError("text is empty")
                    engine, meta = self.slack, h["meta"]
                    if engine is None:
                        raise CoreError("Slack is not configured")
                    self.turn(meta.get("root") or h["handle"], principal)
                    thread = meta["slack_thread"]
                    channel = thread["channel_name"] or thread["channel"]
                    note = f"Note from {meta['boundary']} about {meta['slack_sender']} in #{channel}: {text[:500]}"
                    if meta.get("permalink"):
                        note += f"\n{meta['permalink']}"
                    if not await engine.dm_owner(note):
                        raise CoreError("could not DM the owner")
                    if final is not False:
                        self.inbox.update_handle(h["handle"], status="answered")
                    self.audit.record(origin=principal.origin, source=principal.source, client=principal.client_id, destination="slack",
                                      action="listen_note", outcome="ok", ref=h["handle"], target="owner")
                    return {"conversation": h["handle"], "in_reply_to": h["handle"], "to_owner": True, "delivered": True, "final": final is not False}
                return {**await self.agent_send(principal, to=h["ref"], text=kw.get("text") or "", project=h["project"], in_reply_to=h["handle"],
                                                final=final is not False, _reply=True),
                        "to": f"agent:{h['ref']}", "in_reply_to": h["handle"], "final": final is not False}
            if verb == "result":
                replies = self.mailbox_replies(h["handle"], h["ref"])
                done = next((r for r in reversed(replies) if r.get("final", True)), None)
                return {"conversation": h["handle"], "status": h["status"], "final": done, "replies": replies[-(kw.get("tail") or 10):]}
            if verb == "cancel":
                return {"conversation": h["handle"], "status": h["status"]}
            return {"conversation": h["handle"], "from": h["meta"].get("from"), "status": h["status"], "project": h["project"],
                    "note": "a mailbox message; answer it with reply"}
        if block is None:
            raise CoreError(f"the block behind {ref!r} is not configured")
        action = block.verb(verb)
        if action is None:
            raise CoreError(f"{block.name} conversations take no {verb}")
        return await self.invoke(principal, block.name, action, block.verb_args(verb, ref, {k: v for k, v in kw.items() if v is not None}))

    async def invoke(self, principal: Principal, destination: str, action: Action, kwargs: dict[str, Any], *,
                     handle: str | None = None, handle_meta: dict[str, Any] | None = None) -> Any:
        """Run one destination action for an origin's caller, enforcing the daily cap and writing the audit record."""
        dest = self.destination(destination)
        call = Call(principal=principal, core=self, destination=destination, action=action.name, handle=handle, handle_meta=handle_meta or {})

        def record(outcome: str, detail: str | None = None) -> None:
            if action.is_audited or outcome != "ok":
                self.audit.record(origin=principal.origin, source=principal.source, client=principal.client_id, destination=destination,
                                  action=action.name, outcome=call.notes.get("outcome", outcome) if outcome == "ok" else outcome,
                                  detail=detail or call.notes.get("detail"), target=call.notes.get("target"), ref=call.notes.get("ref"),
                                  title=call.notes.get("title"), excerpt=str(call.notes.get("excerpt", "")))

        if action.capped and dest.daily_cap is not None:
            used = self.audit.count_today(destination, action.name)
            if used >= dest.daily_cap:
                call.note(excerpt=str(kwargs.get("task") or ""))
                record("refused", f"daily cap reached ({used}/{dest.daily_cap})")
                raise DestinationError(f"daily {destination} {action.name} cap reached ({used}/{dest.daily_cap} today, {self.cfg.timezone}); try tomorrow or raise destinations.{destination}.daily_cap")
        try:
            out = await action.handler(call, **kwargs)
        except DestinationError as e:
            record("refused", str(e))
            raise
        except (CoreError, MemoryError_, ProjectError, RouteError) as e:
            record("refused", str(e))
            raise DestinationError(str(e)) from e
        except Exception as e:
            log.exception("%s.%s failed", destination, action.name)
            record("error", f"{type(e).__name__}: {e}"[:300])
            raise DestinationError(f"{destination} {action.name} failed: {e}") from e
        record("ok")
        return out

    # Projects in calls ----------------------------------------------------------------------

    def project(self, slug: str | None) -> Project | None:
        if not slug:
            return None
        try:
            return self.projects.get(slug.strip().lower().removeprefix("project:"))
        except ProjectError as e:
            raise CoreError(str(e)) from e

    # Routes ---------------------------------------------------------------------------------

    async def set_route(self, principal: Principal, *, destination: str, agent: str | None, project: str | None, target: dict[str, Any],
                        defaults: dict[str, Any] | None, allow_override: bool, note: str = "") -> dict[str, Any]:
        dest = self.destination(destination)
        if project:
            self.project(project)
        try:
            target, defaults = await dest.validate_route_target(target, defaults or {})
        except DestinationError as e:
            self.audit.record(origin=principal.origin, source=principal.source, client=principal.client_id, destination=destination,
                              action="route_set", outcome="refused", detail=str(e), target=f"{agent or '*'}/{project or '*'}")
            raise
        r = self.routes.set(destination=destination, agent=agent, project=project, target=target, defaults=defaults,
                            allow_override=allow_override, note=note, by=principal.source)
        self.changed()
        self.audit.record(origin=principal.origin, source=principal.source, client=principal.client_id, destination=destination,
                          action="route_set", outcome="ok", target=f"{r.agent or '*'}/{r.project or '*'}", excerpt=str(r.target))
        return r.as_dict()

    def changed(self) -> None:
        """Routes or projects changed: let listeners (the Slack block's targets) refresh in the background."""
        self._targets.clear()
        for listener in self.change_listeners:
            task = asyncio.ensure_future(listener())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    def remove_route(self, principal: Principal, *, destination: str, agent: str | None, project: str | None) -> dict[str, Any]:
        r = self.routes.remove(destination=destination, agent=agent, project=project)
        self.changed()
        self.audit.record(origin=principal.origin, source=principal.source, client=principal.client_id, destination=destination,
                          action="route_remove", outcome="ok", target=f"{r.agent or '*'}/{r.project or '*'}", excerpt=str(r.target))
        return {"removed": r.as_dict()}

    # Projects: automatic links and routes ------------------------------------------------------

    async def project_sync(self, principal: Principal, slug: str | None = None, force: bool = False) -> dict[str, Any]:
        """Link projects to their destination counterparts and route them, without overwriting anything unless force."""
        projects = [self.projects.get(slug)] if slug else self.projects.list()
        results = []
        for p in projects:
            for name, d in self.destinations.items():
                if type(d).autolink is Block.autolink:
                    continue
                if not d.online:
                    results.append({"project": p.slug, "destination": name, "skipped": f"{name} is offline", "changed": []})
                    continue
                try:
                    out = await d.autolink(principal, p, force=force)
                except Exception as e:
                    log.warning("%s autolink for %s failed: %s", name, p.slug, e)
                    out = {"project": p.slug, "error": str(e), "changed": []}
                if out is not None:
                    results.append({"destination": name, **out})
        return {"results": results, "changed": sum(len(r.get("changed", [])) for r in results)}

    def audit_auto(self, principal: Principal, destination: str, action: str, target: str, excerpt: str) -> None:
        self.audit.record(origin=principal.origin, source="auto", client=principal.client_id, destination=destination,
                          action=action, outcome="ok", target=target, excerpt=excerpt, detail=f"automatic, during a call by {principal.source}")

    def audit_memory(self, principal: Principal, action: str, target: str, excerpt: str = "") -> None:
        self.audit.record(origin=principal.origin, source=principal.source, client=principal.client_id, destination="memory",
                          action=action, outcome="ok", target=target, excerpt=excerpt)

    # Context pack -----------------------------------------------------------------------------

    async def context_pack(self, principal: Principal, project: str | None) -> str:
        p = self.project(project)
        if p and not principal.allows(p.scope):
            raise CoreError(f"{principal.source} may not read {p.scope}")
        if not p and not principal.allows(GENERAL):
            raise CoreError(f"{principal.source} may not read general; pass the project you work in")
        general = principal.allows(GENERAL)
        now = self.clock()
        out = [f"# Shared context (switchboard, {iso(now)}){f' for project {p.slug}' if p else ''}"]

        def section(title: str, entries: list[dict[str, Any]]) -> None:
            if entries:
                out.append(f"\n## {title}")
                out.extend(f"- **{e['title']}** ({e['scope']}/{e['namespace']}, {e['source']}, {e['updated_at'][:10]}, id {e['id']}): {e['body'].strip()}" for e in entries)

        if p:
            out.append(f"\n## Project {p.name} ({p.scope})" + (f"\n{p.description}" if p.description else "") + (f"\nLinks: {p.links}" if p.links else ""))
            if p.archived:
                out.append("This project is archived.")
            section(f"{p.slug}: memory", self.memory.recent(scopes=[p.scope], limit=12, body_limit=500))
        if general:
            section("General: profile", self.memory.recent(scopes=[GENERAL], namespace="profile", limit=8, body_limit=500))
            section("General: recent decisions", self.memory.recent(scopes=[GENERAL], namespace="decisions", since=now - 30 * 86400, limit=8, body_limit=300))
        other = self.memory.recent(scopes=[GENERAL], since=now - 7 * 86400, exclude_namespaces=("profile", "decisions"), limit=10, body_limit=0) if general else []
        if other:
            out.append("\n## General: other changes this week")
            out.extend(f"- {e['title']} ({e['namespace']}, {e['source']}, id {e['id']})" for e in other)
        for name, d in self.destinations.items():
            try:
                text = await d.context_section(principal, p)
            except Exception as e:
                text = f"## {d.title or name}\nCould not read: {e}"
            if text:
                out.append("\n" + text)
        text = "\n".join(out)
        return text[:PACK_LIMIT] + ("\n…(truncated)" if len(text) > PACK_LIMIT else "")
