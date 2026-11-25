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
from .auth import Auth, Principal
from .config import Config, load_config
from .destinations import Action, Call, Destination, DestinationError
from .destinations import load as load_destination
from .inbox import Inbox
from .memory import GENERAL, Memory, MemoryError_
from .origins import Origin
from .origins import load as load_origin
from .projects import Project, ProjectError, Projects
from .routes import RouteError, Routes
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
        self.memory = Memory(self.store, self.projects, self.cfg.mirror_dir)
        self.routes = Routes(self.store)
        self.inbox = Inbox(self.store)
        self.auth = Auth(self.cfg, self.store, http=oauth_http, access_verifier=access_verifier, secret=self._secret())
        self._tasks: set[asyncio.Task[Any]] = set()
        opts = destination_options or {}
        self.destinations: dict[str, Destination] = {
            name: load_destination(name)(self, {**section, **opts.get(name, {})}) for name, section in self.cfg.destinations.items()
        }
        self.origins: dict[str, Origin] = {name: load_origin(name)(self, section) for name, section in self.cfg.origins.items()}

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
        for d in self.destinations.values():
            await d.start()

    async def aclose(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        for d in self.destinations.values():
            await d.aclose()
        await self.auth.http.aclose()
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
                                  action=action.name, outcome=outcome, detail=detail, target=call.notes.get("target"), ref=call.notes.get("ref"),
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
        if action.capped and dest.daily_cap is not None and isinstance(out, dict):
            out.setdefault("used_today", f"{self.audit.count_today(destination, action.name)}/{dest.daily_cap}")
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
        self.audit.record(origin=principal.origin, source=principal.source, client=principal.client_id, destination=destination,
                          action="route_set", outcome="ok", target=f"{r.agent or '*'}/{r.project or '*'}", excerpt=str(r.target))
        return r.as_dict()

    def remove_route(self, principal: Principal, *, destination: str, agent: str | None, project: str | None) -> dict[str, Any]:
        r = self.routes.remove(destination=destination, agent=agent, project=project)
        self.audit.record(origin=principal.origin, source=principal.source, client=principal.client_id, destination=destination,
                          action="route_remove", outcome="ok", target=f"{r.agent or '*'}/{r.project or '*'}", excerpt=str(r.target))
        return {"removed": r.as_dict()}

    # Context pack -----------------------------------------------------------------------------

    async def context_pack(self, principal: Principal, project: str | None) -> str:
        p = self.project(project)
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
        section("General: profile", self.memory.recent(scopes=[GENERAL], namespace="profile", limit=8, body_limit=500))
        section("General: recent decisions", self.memory.recent(scopes=[GENERAL], namespace="decisions", since=now - 30 * 86400, limit=8, body_limit=300))
        other = self.memory.recent(scopes=[GENERAL], since=now - 7 * 86400, exclude_namespaces=("profile", "decisions"), limit=10, body_limit=0)
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
