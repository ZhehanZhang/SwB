"""The Slack origin: events from the owner's Slack workspaces (watched channels, mentions, DMs) arrive here.

The shared Slack engine runs the intake (Socket Mode per workspace, or polling), dedupes events, drops our own posts,
marks what is addressed to us and routes each event to a target ("@swb <slug> ..."; see slack/routing.py). This origin:

- keeps the engine's targets current: Captains from the Capy destination, agents switchboard knows, and projects
  (at startup, every refresh_minutes, and after a route change), plus a persona and avatar per Captain;
- files each event in the inbox of every agent with a Slack grant on that workspace, and an event addressed to an
  agent target in that agent's inbox as well;
- delegates an addressed event for a Captain or project target that no webhook sink of the workspace takes, as
  agent "slack" through the Capy destination, and posts the Captain's final reply back in the Slack thread under
  the Captain's persona.

origins.slack.workspaces.<label>.project names the switchboard project a workspace's events belong to (a project
filter on the inbox then finds them).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any, AsyncIterator

from .. import Origin
from ...auth import Principal
from ...slack.capy_avatars import render_captain
from ...slack.config import Grant, Persona
from ...slack.routing import build_targets, slugify

if TYPE_CHECKING:
    from ...core import Switchboard

log = logging.getLogger("switchboard.origins.slack")
AGENT = "slack"
RELAY_READER = "slack-relay"


class SlackOrigin(Origin):
    name = "slack"

    def __init__(self, core: "Switchboard", config: dict[str, Any]):
        super().__init__(core, config)
        self.projects = {str(k): str((v or {}).get("project") or "") or None for k, v in (config.get("workspaces") or {}).items()}
        self.refresh_s = float(config.get("refresh_minutes", 30)) * 60
        self.principal = Principal(source=AGENT, client_id="slack-origin", kind="slack", origin="slack")
        self.refreshed_at: float | None = None
        self.refresh_error: str | None = None
        self._refresh_lock = asyncio.Lock()
        if core.slack is not None:
            core.slack.listeners.append(self.deliver)
            core.change_listeners.append(self.changed)

    def problems(self) -> list[str]:
        return [] if self.core.slack is not None else ["origins.slack is configured but the config has no slack section"]

    @contextlib.asynccontextmanager
    async def _run(self) -> AsyncIterator[None]:
        tasks = [asyncio.create_task(self._refresh_loop(), name="slack-targets"), asyncio.create_task(self._relay_loop(), name="slack-relay")]
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def lifespan(self) -> contextlib.AbstractAsyncContextManager[None] | None:
        return self._run() if self.core.slack is not None else None

    # Targets ---------------------------------------------------------------------------------

    async def _refresh_loop(self) -> None:
        while True:
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("slack targets refresh failed")
                self.refresh_error = str(exc)[:300]
            await asyncio.sleep(self.refresh_s)

    async def changed(self) -> None:
        if self.core.slack is not None:
            await self.refresh()

    async def refresh(self) -> dict[str, Any]:
        engine = self.core.slack
        assert engine is not None
        async with self._refresh_lock:
            captains: list[dict[str, Any]] = []
            capy = self.core.destinations.get("capy")
            api = getattr(capy, "api", None)
            if api is not None:
                try:
                    captains = await api.list_captains()
                    self.refresh_error = None
                except Exception as exc:
                    self.refresh_error = f"GET /captains failed: {exc}"[:300]
                    captains = [{"id": t.id, "name": t.name} for t in engine.targets.values() if t.kind == "captain"]
            projects = [{"slug": p.slug, "name": p.name} for p in self.core.projects.list()]
            targets = build_targets(captains, self.core.known_agents(), projects, engine.config.target_aliases)
            personas: dict[str, Persona] = {}
            for c in captains:
                pid = slugify(c.get("name") or "")
                if not pid or pid in personas:
                    continue
                avatar = await render_captain(engine.http, engine.avatar_cache, c.get("name") or pid, c.get("avatar"), engine.config.captain_avatars)
                personas[pid] = Persona(id=pid, name=c.get("name") or pid, avatar=avatar)
            by_captain = {c["id"]: slugify(c.get("name") or "") for c in captains}
            for slug, t in list(targets.items()):
                if t.kind == "project":
                    route, _ = self.core.routes.resolve(destination="capy", agent=AGENT, project=t.id)
                    captain = (route.target or {}).get("captain") if route else None
                    if captain in by_captain:
                        targets[slug] = type(t)(t.slug, t.kind, t.id, t.name, persona=by_captain[captain])
            engine.set_targets(targets, personas)
            self.refreshed_at = self.core.clock()
            return self.describe()

    def describe(self) -> dict[str, Any]:
        engine = self.core.slack
        assert engine is not None
        return {"targets": [t.as_dict() for t in sorted(engine.targets.values(), key=lambda t: (t.kind, t.slug))],
                "defaults": {label: ws.default_target for label, ws in engine.config.workspaces.items()},
                "aliases": dict(engine.config.target_aliases), "refreshed_at": self.refreshed_at, "error": self.refresh_error,
                "usage": "Mention the bot, then a target: '@bot teamcaptain deploy status?'. Without one, the thread's target or the workspace default."}

    # Events ----------------------------------------------------------------------------------

    def _handle(self, agent: str, workspace: str) -> str:
        for h in self.core.inbox.handles(owner=agent, destination="slack", limit=200):
            if h["ref"] == workspace:
                return h["handle"]
        return self.core.inbox.open_handle(owner=agent, destination="slack", ref=workspace, project=self.projects.get(workspace),
                                           title=f"Slack {workspace}", meta={"workspace": workspace}, status="following")

    async def deliver(self, push: dict[str, Any]) -> None:
        engine = self.core.slack
        assert engine is not None
        sender = {"kind": "slack", "workspace": push["workspace"], "team": push["team"], "channel": push["channel"], "channel_name": push["channel_name"],
                  "user": push["user"], "user_name": push["user_name"], "is_bot": push["is_bot"], "ts": push["ts"], "thread_ts": push["thread_ts"],
                  "permalink": push["permalink"]}
        item = {"event_id": push["id"], "slack_kind": push["kind"], "addressed_to_us": push["addressed_to_us"], "text": push["text"],
                "truncated": push["truncated"], "files": push["files"], "target": push.get("target"), "target_kind": push.get("target_kind"), "from": sender}
        agents = {agent for agent, grant in engine.config.grants.items() if grant.identities_for(push["workspace"])}
        target = engine.targets.get(push.get("target") or "")
        if target is not None and target.kind == "agent" and push["addressed_to_us"]:
            agents.add(target.id)
        for agent in sorted(agents):
            await self.core.inbox.add(self._handle(agent, push["workspace"]), "slack_event", item)
        if target is not None and target.kind in ("captain", "project") and push["addressed_to_us"] and not engine.sink_accepts(push["workspace"], push):
            await self.delegate(push, target)

    async def delegate(self, push: dict[str, Any], target: Any) -> None:
        engine = self.core.slack
        assert engine is not None
        where = f"#{push['channel_name']}" if push.get("channel_name") else push["channel"]
        task = (f"Slack message from {push['user_name'] or push['user']} in {push['workspace']} {where} ({push['permalink'] or push['ts']}):\n\n"
                f"{push['text']}\n\nYour final reply is posted back in that Slack thread as {target.name}.")
        kw: dict[str, Any] = {"task": task, "title": f"Slack {push['workspace']} {where}"}
        if target.kind == "captain":
            kw["target"] = f"captain:{target.id}"
        else:
            kw["project"] = target.id
        try:
            dest = self.core.destination("capy")
            out = await self.core.invoke(self.principal, "capy", dest.action("delegate"), kw)
        except Exception as exc:
            log.warning("slack %s: delegating %s to %s failed: %s", push["workspace"], push["id"], target.slug, exc)
            engine.store.audit(f"agent:{AGENT}", "route_failed", push["workspace"], event=push["id"], target=target.slug, error=str(exc)[:300])
            return
        engine.store.add_relay(out["handle"], push["workspace"], push["channel"], push["thread_ts"] or push["ts"], target.slug)
        engine.store.audit(f"agent:{AGENT}", "routed", push["workspace"], event=push["id"], target=target.slug, handle=out["handle"])

    async def _relay_loop(self) -> None:
        while True:
            try:
                got = await self.core.inbox.wait(owner=AGENT, destination="capy", timeout=60, reader=RELAY_READER)
                for item in got.get("items", []):
                    await self.relay(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("slack relay failed")
                await asyncio.sleep(5)

    async def relay(self, item: dict[str, Any]) -> None:
        """Post a Captain's final reply to a routed Slack message back in its thread, as the Captain's persona."""
        engine = self.core.slack
        assert engine is not None
        if item.get("kind") != "final" or not (item.get("text") or "").strip():
            return
        r = engine.store.relay(item["handle"])
        if r is None:
            return
        target = engine.targets.get(r["target"])
        key = Grant(id=AGENT, persona=target.persona if target else None, allow_persona_override=False, allowed_personas=None, scopes={r["workspace"]: ("bot",)})
        await engine.gate.submit(key, r["workspace"], "bot", "post_message", {"channel": r["channel"], "text": item["text"][: engine.config.max_text_chars], "thread_ts": r["thread_ts"]})
