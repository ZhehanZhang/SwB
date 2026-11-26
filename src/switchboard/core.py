"""The core: auth, projects and scoped memory, routes, the inbox, audit and caps, and routing from origins to destinations."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from .audit import Audit, iso
from .auth import AGENT_LABELS, Auth, Principal
from .config import Config, load_config
from .destinations import Action, Call, Destination, DestinationError
from .destinations import load as load_destination
from .inbox import Inbox
from .memory import GENERAL, Memory, MemoryError_
from .origins import Origin
from .origins import load as load_origin
from .projects import Project, ProjectError, Projects
from .routes import RouteError, Routes
from .slack.config import parse_config as parse_slack_config
from .slack.engine import SlackEngine
from .store import Store

log = logging.getLogger("switchboard")

PACK_LIMIT = 14_000


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
        self.auth = Auth(self.cfg, self.store, http=oauth_http, access_verifier=access_verifier, secret=self._secret())
        self.auth.audit = self.audit
        self._tasks: set[asyncio.Task[Any]] = set()
        self.change_listeners: list[Any] = []
        slack_opts = dict(slack_options or {})
        self.slack_connect = bool(slack_opts.pop("connect", True))
        self.slack: SlackEngine | None = None
        destinations, origins = dict(self.cfg.destinations), dict(self.cfg.origins)
        if self.cfg.slack is not None:
            self.slack = SlackEngine(parse_slack_config(self.cfg.slack, self.cfg.env), self.data_dir, env=self.cfg.env, **slack_opts)
            destinations.setdefault("slack", {})
            origins.setdefault("slack", {})
        opts = destination_options or {}
        self.destinations: dict[str, Destination] = {
            name: load_destination(name)(self, {**section, **opts.get(name, {})}) for name, section in destinations.items()
        }
        self.origins: dict[str, Origin] = {name: load_origin(name)(self, section) for name, section in origins.items()}

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
        if self.slack is not None:
            await self.slack.start(connect=self.slack_connect)
        for d in self.destinations.values():
            await d.start()

    async def aclose(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        for d in self.destinations.values():
            await d.aclose()
        if self.slack is not None:
            await self.slack.stop()
        await self.auth.http.aclose()
        if self.memory.mirror_pending:
            self.memory.flush_mirror()
        self.store.close()

    def health(self) -> dict[str, Any]:
        problems = list(self.cfg.problems())
        for o in self.origins.values():
            problems += o.problems()
        for d in self.destinations.values():
            problems += d.problems()
        return {
            "status": "degraded" if problems else "ok",
            "problems": problems,
            "oauth": bool(self.cfg.issuer),
            "origins": sorted(self.origins),
            "destinations": {n: {"online": d.online, **d.status()} for n, d in self.destinations.items()},
            "static_keys": [k.name for k in self.cfg.static_keys],
            "mirror": bool(self.cfg.mirror_dir),
        }

    # Routing an action to a destination -----------------------------------------------------

    def known_agents(self) -> list[str]:
        """Agent names a message can be addressed to: OAuth agents, bearer-key names, and agents named in routes."""
        names = set(AGENT_LABELS) | {k.name for k in self.cfg.static_keys if k.name not in self.cfg.inbox_keys} | set(self.cfg.source_names.values())
        names |= set(self.cfg.inbox_keys.values())
        names |= {r["agent"] for r in self.store.q("SELECT DISTINCT agent FROM routes WHERE agent IS NOT NULL AND agent != ''")}
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
        sender = {"kind": "agent", "agent": principal.source, "label": AGENT_LABELS.get(principal.source, principal.source),
                  "auth": principal.kind, "client_id": principal.client_id, "origin": principal.origin}
        handle = self.inbox.open_handle(owner=to, destination="mailbox", ref=principal.source, project=p.slug if p else None, title=None,
                                        meta={"from": sender}, status="delivered")
        self.inbox.update_handle(handle, open=False)
        await self.inbox.add(handle, "message", {"text": text[:20_000], "project": p.slug if p else None, "from": sender})
        self.audit.record(origin=principal.origin, source=principal.source, client=principal.client_id, destination="mailbox", action="agent_send",
                          outcome="ok", target=to, ref=handle, excerpt=text)
        return {"to": to, "handle": handle, "delivered": True, "project": p.slug if p else None}

    def destination(self, name: str) -> Destination:
        d = self.destinations.get(name)
        if d is None:
            raise CoreError(f"destination {name!r} is not configured; configured: {', '.join(self.destinations) or 'none'}")
        return d

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
                                       f"was stopped. Check capy_inbox for the work already sent; capy_reply and capy_wait are not limited.")
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
        """Routes or projects changed: let listeners (the Slack origin's targets) refresh in the background."""
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
                if type(d).autolink is Destination.autolink:
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
