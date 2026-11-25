"""The core: blocks, agents, identities, targets, conversations (the inbox), routes, projects and scoped memory, approvals and audit.

It names no vendor. Blocks bring the ways in and out; the core routes a caller's verb (send, reply, status, result,
cancel, targets) to the block that owns the target, through audit and caps.
"""

from __future__ import annotations

import asyncio
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
from .config import Config, load_config
from .connect import Connections
from .inbox import Inbox
from .memory import GENERAL, Memory, MemoryError_
from .projects import Project, ProjectError, Projects
from .routes import RouteError, Routes
from .store import Store
from .trust import allows

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

    def health(self) -> dict[str, Any]:
        problems = list(self.cfg.problems())
        for b in self.blocks.values():
            problems += b.problems()
        return {
            "status": "degraded" if problems else "ok",
            "problems": problems,
            "oauth": bool(self.cfg.issuer),
            "blocks": {n: {"use": b.manifest.id, "kind": b.manifest.kind, "level": {"out": b.manifest.level_out, "in": b.manifest.level_in},
                           "online": b.online} for n, b in self.blocks.items()},
            "origins": sorted(self.origins),
            "destinations": {n: {"online": d.online, **d.status()} for n, d in self.destinations.items()},
            "static_keys": [k.name for k in self.cfg.static_keys],
            "mirror": bool(self.cfg.mirror_dir),
        }

    # Routing an action to a destination -----------------------------------------------------

    def known_agents(self) -> list[str]:
        """Agent names a message can be addressed to: OAuth agents, bearer-key names, and agents named in routes."""
        names = set(self.agents.ids()) | {k.name for k in self.cfg.static_keys if k.name not in self.cfg.inbox_keys} | set(self.cfg.source_names.values())
        names |= set(self.cfg.inbox_keys.values())
        names |= {r["agent"] for r in self.store.q("SELECT DISTINCT agent FROM routes WHERE agent IS NOT NULL AND agent != ''")}
        names |= set(self.connections.agents())
        return sorted(names)

    async def agent_send(self, principal: Principal, *, to: str, text: str, project: str | None = None) -> dict[str, Any]:
        """Put a message in another agent's mailbox. The sender is the authenticated caller, never anything in the text."""
        to = (to or "").strip().lower()
        if to not in self.known_agents():
            raise CoreError(f"no agent {to!r}; known agents: {', '.join(self.known_agents())}")
        if to == principal.source:
            raise CoreError("that is you; agent_send goes to another agent")
        text = (text or "").strip()
        if not text:
            raise CoreError("text is empty")
        p = self.project(project)
        sender = {"kind": "agent", "agent": principal.source, "label": self.agents.label(principal.source),
                  "auth": principal.kind, "client_id": principal.client_id, "origin": principal.origin}
        handle = self.inbox.open_handle(owner=to, destination="mailbox", ref=principal.source, project=p.slug if p else None, title=None,
                                        meta={"from": sender}, status="delivered", target=f"agent:{to}")
        self.inbox.update_handle(handle, open=False)
        await self.inbox.add(handle, "message", {"text": text[:20_000], "project": p.slug if p else None, "from": sender})
        self.audit.record(origin=principal.origin, source=principal.source, client=principal.client_id, destination="mailbox", action="agent_send",
                          outcome="ok", target=to, ref=handle, excerpt=text)
        return {"to": to, "handle": handle, "conversation": handle, "delivered": True, "project": p.slug if p else None}

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
        if sep and head.lower() in tgt.LEGACY_KINDS:
            owner = self._kind_owner(head.lower())
            if owner:
                return Address(owner, head.lower(), rest.strip())
        word = text.lower()
        if word in self.blocks:
            return Address(word, "", "")
        if word in self.known_agents():
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

    async def send(self, principal: Principal, *, task: str, to: str | None = None, project: str | None = None, title: str | None = None,
                   files: list[dict[str, Any]] | None = None, repos: list[str] | None = None, new_conversation: bool | None = None) -> dict[str, Any]:
        """Send work or a message. Without to, the route for the caller and project decides; agent:<x> leaves it in a mailbox."""
        addr = await self.resolve_to(to) if to else None
        if addr is not None and addr.block == "agent":
            return {**await self.agent_send(principal, to=addr.id, text=task, project=project), "to": str(addr)}
        if addr is not None and addr.block == "project":
            project, addr = addr.id, None
        if addr is not None:
            block = self.destination(addr.block)
        else:
            block = self._routing_block(principal, project)
        action = block.verb("send")
        if action is None:
            raise CoreError(f"{block.name} takes no send; its tools are {', '.join(block.manifest.tools) or 'none'}")
        if principal.kind == "connect" and not allows("known", str(block.config.get("allow_from") or "owner")):
            self.audit.record(origin=principal.origin, source=principal.source, client=principal.client_id, destination=block.name, action="send", outcome="refused", target=to)
            raise CoreError(f"{block.name} takes work only from the owner's own sign-ins; a connected agent can send to agent:<name> mailboxes, "
                            f"or the owner can set blocks.{block.name}.allow_from: known")
        message = {"task": task, "title": title, "project": project, "repos": repos, "files": files, "new_thread": new_conversation}
        kw = block.send_args(addr if addr is not None and (addr.kind or addr.id) else None, {k: v for k, v in message.items() if v is not None})
        out = await self.invoke(principal, block.name, action, kw)
        if isinstance(out, dict):
            out.setdefault("conversation", out.get("handle"))
            out.setdefault("block", block.name)
        return out

    def _routing_block(self, principal: Principal, project: str | None) -> Block:
        routable = [b for b in self.blocks.values() if b.supports_routes and b.verb("send") is not None]
        p = project.strip().lower().removeprefix("project:") if project else None
        for b in routable:
            if self.routes.resolve(destination=b.name, agent=principal.source, project=p)[0] is not None:
                return b
        if routable:
            return routable[0]
        raise CoreError("nothing to send to: no block that takes routes is configured; pass to (an agent:<name> mailbox or a block target)")

    def _conversation(self, principal: Principal, ref: str) -> tuple[Block | None, dict[str, Any] | None]:
        """The block and handle behind a conversation reference (h_ handle, or a block's own id such as a thread id)."""
        ref = (ref or "").strip()
        if ref.startswith("h_"):
            h = self.inbox.handle(ref)
            if h is None or h["owner"] != principal.source:
                raise CoreError(f"no conversation {ref!r}")
            return self.blocks.get(h["destination"]), h
        owner = next((b for b in self.blocks.values() if b.owns_ref(ref)), None)
        if owner is None:
            raise CoreError(f"no block owns {ref!r}; pass the conversation handle (h_...)")
        return owner, None

    async def verb(self, principal: Principal, verb: str, ref: str, **kw: Any) -> Any:
        """reply, status, result or cancel on a conversation, run by the block that owns it."""
        block, h = self._conversation(principal, ref)
        if h is not None and h["destination"] == "mailbox":
            if verb == "reply":
                return await self.send(principal, to=f"agent:{h['ref']}", task=kw.get("text") or "", project=h["project"])
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

    async def invoke(self, principal: Principal, destination: str, action: Action, kwargs: dict[str, Any]) -> Any:
        """Run one destination action for an origin's caller, enforcing the daily cap and writing the audit record."""
        dest = self.destination(destination)
        call = Call(principal=principal, core=self, destination=destination, action=action.name)

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
        if action.burst and dest.burst_limit:
            count, window = dest.burst_limit
            used = self.audit.count_since(destination, action.name, principal.source, self.clock() - window)
            if used >= count:
                call.note(excerpt=str(kwargs.get("task") or ""))
                record("refused", f"burst limit ({used} in {window:.0f} s)")
                raise DestinationError(f"{used} {action.name} calls from {principal.source} in the last {window / 60:.0f} minutes looks like a loop, so this one "
                                       f"was stopped. Check inbox for the work already sent; reply and wait are not limited.")
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
