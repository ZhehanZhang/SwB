"""The Capy block: send work to Capy threads and Captains, follow them over SSE, and report back into the inbox.

Captains, threads and crews exist only here, as target kinds and block tools (capy_captain_digest, capy_captains,
capy_list). Capy's own sign-in (the capy agent), its project links (links.capy_project) and its Captain personas come
from this block's manifest, so the core never names Capy.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import httpx

from ...audit import iso
from ...block_env import CAPY_AGENT_KEY_PREFIX as AS_PREFIX
from ...block_env import CAPY_KEY, CAPY_SEND_KEY
from ...claims import ClaimInputError
from ...refusals import CONVERSATION_REQUIRED, SCOPE_REQUIRED, TARGET_REQUIRED
from ...subject import display_subject, lead_with_subject, subject
from ...targets import CAPTAIN_SLUG, CaptainTargetError, captain_selection_error
from .. import Action, Address, AgentSpec, Block, BlockError, BlockRefused, Call, Manifest, TargetKind
from .api import CapyAPI, CapyError, pr_links, thread_summary
from .stream import Backoff, Frame, StreamClosed, ThreadStream

DestinationError = BlockError

if TYPE_CHECKING:
    from ...auth import Principal
    from ...core import Switchboard
    from ...projects import Project

log = logging.getLogger("switchboard.capy")

CAPY_KEY_RE = re.compile(r"capy_[A-Za-z0-9_-]{43}")
MSG_LIMIT = 1500
RESULT_LIMIT = 20_000
DIGEST_LIMIT = 2500
DELIVERIES = ("queue", "steer", "interrupt")
MODES = ("captain_thread", "new_thread")
TARGET_KINDS = ("captain", "project", "thread")
DEFAULT_KEYS = {"model", "repos", "mode", "delivery"}

INSTRUCTIONS = """Capy is a coding agent with cloud machines and Captains. Send it work that needs a machine, repository, or pull request.
Use send(task=...) to follow the owner's route, usually to their Captain. Set to='capy:captain:<name>' if the route lets you choose.
Call wait for the reply. Use capy_captain_digest, capy_captains and capy_list to read about Captains and their crews."""


def _api_timestamp(value: Any) -> str | None:
    if isinstance(value, str):
        try:
            if datetime.fromisoformat(value).tzinfo is not None:
                return value
        except ValueError:
            pass
    return None


def parse_target(text: str) -> dict[str, Any]:
    """'captain:<id|name>', 'project:<id|code|name>', 'thread:<jam_id>', or a bare jam_/project_ id or Captain name."""
    text = (text or "").strip()
    kind, sep, value = text.partition(":")
    if sep and kind.lower() in TARGET_KINDS and value.strip():
        if kind.casefold() == CAPTAIN_SLUG and value.strip().casefold() == CAPTAIN_SLUG:
            return {CAPTAIN_SLUG: value.strip(), "explicit_name": True}
        return {kind.lower(): value.strip()}
    if text.startswith("jam_"):
        return {"thread": text}
    if text.startswith("project_"):
        return {"project": text}
    if not text:
        raise DestinationError("target is empty")
    return {"captain": text}


def describe(target: dict[str, Any]) -> str:
    for kind in TARGET_KINDS:
        if target.get(kind):
            name = target.get("name")
            return f"{kind} {name} ({target[kind]})" if name else f"{kind} {target[kind]}"
    return str(target)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40]


def validate_api_key(value: str) -> None:
    if not CAPY_KEY_RE.fullmatch(value):
        raise ClaimInputError("Enter capy_ followed by 43 characters")


class CapyBlock(Block):
    connect_kinds = ("capy-captain",)

    manifest = Manifest(
        id="capy",
        title="Capy",
        kind="sink",
        level_out="L4",
        level_in="mcp",
        verbs={"send": "delegate", "reply": "reply", "status": "status", "result": "result", "cancel": "cancel"},
        tools=("captain_digest", "captains", "list"),
        targets=(TargetKind("captain", slug_from="name", persona=True), TargetKind("project", ref_prefix="project_"),
                 TargetKind("thread", ref_prefix="jam_")),
        agents=(AgentSpec("capy", "Capy", redirect_uris=("https://capy.ai/web/callbacks/mcp",), redirect_hosts=("capy.ai",), cimd_hosts=("capy.ai",),
                          link_key="capy_project"),),
        personas="dynamic",
        link_keys=("capy_project",),
        secrets=(CAPY_KEY, CAPY_SEND_KEY, f"{AS_PREFIX}*"),
        network=("api.capy.ai",),
        identities=({"id": "service", "policy": "free"}, {"id": "personal", "policy": "free", "optional": True}),
        config_schema={"type": "object", "properties": {
            "api_base": {"type": "string"}, "identity": {"enum": ["service", "personal"]}, "send_key_env": {"type": "string"},
            "default_route": {}, "daily_cap": {"type": ["integer", "null"]}, "captain_delivery": {"enum": list(DELIVERIES)},
            "avatars": {"type": "boolean"}, "handle_ttl_days": {"type": "number"}, "duplicate_window_s": {"type": "number"}}},
        instructions=INSTRUCTIONS,
    )

    def __init__(self, core: "Switchboard", config: dict[str, Any]):
        super().__init__(core, config)
        key = core.secret(CAPY_KEY)
        base = str(config.get("api_base", "https://api.capy.ai/api/v1"))
        transport = config.get("transport")
        self.api: CapyAPI | None = CapyAPI(base, key, transport=transport) if key else None
        self.send_key_env = str(config.get("send_key_env") or CAPY_SEND_KEY)
        self.identity = str(config.get("identity", "service"))
        if self.identity not in ("service", "personal"):
            raise ValueError("destinations.capy.identity must be service or personal")
        self.keys: dict[str, CapyAPI] = {}
        if self.api is not None:
            self.keys[CAPY_KEY] = self.api
            if self.send_key_env != CAPY_KEY and (send_key := core.secret(self.send_key_env)):
                self.keys[self.send_key_env] = CapyAPI(base, send_key, transport=transport)
            for env, value in sorted(core.cfg.env.items()):
                if value and env not in self.keys and env.startswith(AS_PREFIX) and len(env) > len(AS_PREFIX):
                    self.keys[env] = CapyAPI(base, value, transport=transport)
        self.key_checks: dict[str, dict[str, Any]] = {}
        self.check_interval_s = float(config.get("key_check_interval_s", 1800))
        self._titles: dict[str, str] = {}
        self.delivery = str(config.get("captain_delivery", "queue"))
        if self.delivery not in DELIVERIES:
            raise ValueError("destinations.capy.captain_delivery must be queue, steer or interrupt")
        self.max_file_bytes = int(config.get("max_file_bytes", 10 * 1024 * 1024))
        self.handle_ttl = float(config.get("handle_ttl_days", 7)) * 86400
        self.sleep: Callable[[float], Awaitable[None]] = config.get("sleep") or asyncio.sleep
        self.backoff_base = float(config.get("reconnect_base_s", 1.0))
        self.followers: dict[str, ThreadStream] = {}
        self._follow_tasks: dict[str, asyncio.Task[Any]] = {}
        self._seed_route(config.get("default_route"))

    def bind(self) -> None:
        self.core.claims.register_name(CAPY_KEY, validate_api_key)
        self.core.claims.register_name(self.send_key_env, validate_api_key)

    # Lifecycle and health ----------------------------------------------------------------

    def _seed_route(self, raw: Any) -> None:
        """destinations.capy.default_route seeds the global route once, so a fresh deploy can delegate before anyone calls routes_set."""
        if not raw:
            return
        routes = self.core.routes
        if routes.resolve(destination=self.name, agent="", project=None)[0] is not None:
            return
        target = parse_target(raw) if isinstance(raw, str) else {k: v for k, v in dict(raw).items() if k in TARGET_KINDS}
        defaults = {} if isinstance(raw, str) else {k: v for k, v in dict(raw).items() if k in DEFAULT_KEYS}
        routes.set(destination=self.name, agent=None, project=None, target=target, defaults=defaults, allow_override=True,
                   note="seeded from destinations.capy.default_route", by="config")

    @property
    def online(self) -> bool:
        return self.api is not None

    def problems(self) -> list[str]:
        if self.api is None:
            return ["CAPY_API_KEY is not set: the capy block is offline (memory, projects and routes still work)"]
        out = []
        if self.identity == "service" and self.send_key_env not in self.keys:
            out.append(f"{self.send_key_env} (destinations.capy.send_key_env) is not set: sends from agents without their own "
                       f"CAPY_API_KEY_AS_<AGENT> are refused (set it, or destinations.capy.identity: personal to send as CAPY_API_KEY)")
        out += [f"{env}: {c['status']} ({c['detail']})" for env, c in sorted(self.key_checks.items()) if c["status"] != "ok"]
        return out

    @staticmethod
    def as_env(agent: str) -> str:
        return AS_PREFIX + re.sub(r"[^A-Z0-9]+", "_", agent.upper()).strip("_")

    def chain(self, agent: str | None) -> list[str]:
        """Keys a send tries, in order: the agent's own CAPY_API_KEY_AS_<AGENT>, then the service user's send_key_env.
        CAPY_API_KEY (often the owner's own key) sends only with identity: personal, so relayed messages never appear as
        the owner unless that was chosen."""
        envs = [self.as_env(agent) if agent else "", self.send_key_env, CAPY_KEY if self.identity == "personal" else ""]
        return list(dict.fromkeys(e for e in envs if e in self.keys))

    def agents(self) -> list[str]:
        """Agents to show a sender for: the core's known agents plus any agent that only has a CAPY_API_KEY_AS_ key."""
        known = set(self.core.known_agents())
        covered = {self.as_env(a) for a in known}
        known |= {e[len(AS_PREFIX):].lower().replace("_", "-") for e in self.keys if e.startswith(AS_PREFIX) and e not in covered}
        return sorted(known)

    def sending(self, call: Call) -> Callable[[Callable[[CapyAPI], Awaitable[Any]]], Awaitable[Any]]:
        """Run send operations down the caller's key chain. When Capy answers 401 or 403, the next key takes this and the
        rest of the call's sends, and the fallback is written to the audit log."""
        self.need_api()
        envs = self.chain(call.principal.source)
        if not envs:
            raise DestinationError(f"no Capy key may send for {call.principal.source}: set {self.as_env(call.principal.source)} or {self.send_key_env} "
                                   "(a service user's key). Switchboard never sends as CAPY_API_KEY unless destinations.capy.identity is personal.")
        pos, refused = [0], []
        call.note(detail=f"sent as {envs[0]}")

        async def run(op: Callable[[CapyAPI], Awaitable[Any]]) -> Any:
            while True:
                env = envs[pos[0]]
                try:
                    return await op(self.keys[env])
                except CapyError as e:
                    if e.status not in (401, 403):
                        raise
                    refused.append(f"{env} was refused ({e.status})")
                    pr = call.principal
                    if pos[0] + 1 >= len(envs):
                        detail = "not sent: " + ", ".join(refused)
                        call.note(detail=detail)
                        self.core.audit.record(origin=pr.origin, source=pr.source, client=pr.client_id, destination=self.name, action="send_refused",
                                               outcome="refused", target=env, detail=detail)
                        raise DestinationError(f"every send key was refused ({', '.join(refused)}); give that Capy user access to the "
                                               "project and the device, or add a key. Nothing was sent as the owner.") from e
                    pos[0] += 1
                    detail = f"sent as {envs[pos[0]]}: " + ", ".join(refused)
                    log.warning("capy send key fallback: %s", detail)
                    call.note(detail=detail)
                    self.core.audit.record(origin=pr.origin, source=pr.source, client=pr.client_id, destination=self.name, action="send_key_fallback",
                                           outcome="ok", target=env, detail=detail)

        return run

    async def check_keys(self) -> None:
        """Check every configured Capy key against the routed Captains and threads: can it see them, and can it send there."""
        captains, _, threads = self._routed()
        for env, api in list(self.keys.items()):
            self.key_checks[env] = {**await self._check_key(api, sorted(captains), sorted(threads)), "at": iso(self.core.clock())}

    @staticmethod
    async def _check_key(api: CapyAPI, captains: list[str], threads: list[str]) -> dict[str, Any]:
        try:
            seen = {c.get("id") for c in await api.list_captains()}
        except CapyError as e:
            return {"status": "rejected" if e.status == 401 else "error", "detail": f"GET /captains: {e}"}
        missing = [c for c in captains if c not in seen]
        for t in threads:
            try:
                await api.get_thread(t)
            except CapyError as e:
                if e.status not in (403, 404):
                    return {"status": "error", "detail": f"GET thread {t}: {e}"}
                missing.append(t)
        if missing:
            return {"status": "no project access", "detail": "cannot see " + ", ".join(missing)}
        for t in [*captains, *threads]:
            try:
                await api.probe_send(t)
            except CapyError as e:
                if e.status == 403:
                    return {"status": "no device access", "detail": f"sending to {t}: {e}"}
                return {"status": "error", "detail": f"send probe on {t}: {e}"}
        n = len(captains) + len(threads)
        return {"status": "ok", "detail": f"sees and can send to {n} routed Captain{'s' if n != 1 else ''} or thread{'s' if n != 1 else ''}"}

    async def _check_loop(self) -> None:
        while True:
            try:
                await self.check_keys()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("checking Capy keys failed")
            await asyncio.sleep(self.check_interval_s)

    def status(self) -> dict[str, Any]:
        if self.api is None:
            return {"sends_as": None, "following": [], "open_handles": 0}
        return {"identity": self.identity, "sends_as": (self.chain(None) or [None])[0], "sends_as_agents": {a: (self.chain(a) or [None])[0] for a in self.agents()},
                "send_keys": {env: self.key_checks.get(env, {"status": "unchecked"}) for env in self.keys}, "following": sorted(self.followers), "open_handles": len(self.core.inbox.handles(destination=self.name, open_only=True, limit=500))}

    def status_for(self, principal: "Principal") -> dict[str, Any]:
        if principal.is_unrestricted_owner:
            return self.status()
        handles = self.core.inbox.handles(owner=principal.source, destination=self.name, open_only=True, scopes=principal.scopes, limit=500)
        return {"open_handles": len(handles)}

    def config_step(self) -> str | None:
        if self.api is None:
            if self.core.secret(CAPY_KEY):
                return "Restart Switchboard to load the saved Capy API key."
            return "Create a key in Capy Settings → API and enter it through the private link from connect(what='capy-captain')."
        if not self._routed()[0]:
            return "Call connect(what='capy-captain') to route a Capy Captain."
        if self.identity == "service" and self.send_key_env not in self.keys and not any(env.startswith(AS_PREFIX) for env in self.keys):
            if self.core.secret(self.send_key_env):
                return "Restart Switchboard to load the saved Capy service key."
            return "Call connect(what='capy-captain') to save a Capy service user's key."
        return None

    def next_step(self) -> str | None:
        if self.api is not None and self.key_checks.get(CAPY_KEY, {}).get("status") == "rejected":
            return "Replace the rejected Capy API key and restart Switchboard."
        return self.config_step()

    async def connect_steps(self, kind: str, principal: Principal, project: str | None,
                            choose: dict[str, Any] | None) -> dict[str, Any] | None:
        if kind not in self.connect_kinds:
            return None
        if not principal.is_unrestricted_owner:
            return {"done": [], "next": [{"by": "person", "do": "Ask the owner to connect a Capy Captain from an unrestricted Switchboard client."}]}
        has_key = bool(self.core.secret(CAPY_KEY))
        done = [f"Capy API key: {'present' if has_key else 'absent'}."]
        if self.api is None:
            if has_key:
                return {"done": done, "next": [{"by": "person", "do": self.next_step()}]}
            step = self.token_step(principal, CAPY_KEY, self.next_step())
            return {"done": done, "next": [step]}
        choose = choose or {}
        if set(choose) - {"captain", "project"}:
            raise BlockError("choose accepts captain (a Capy Captain's ID, code, or name) and project (a Switchboard project slug)")
        if any(not isinstance(value, str) or not value.strip() for value in choose.values()):
            raise BlockError("choose.captain and choose.project must be nonempty strings")
        p = self.core.project(project or choose.get("project"))
        if project and "project" in choose and self.core.project(choose["project"]).slug != p.slug:
            raise BlockError("project and choose.project must name the same Switchboard project")
        if p and p.archived:
            raise BlockError("Choose an active Switchboard project for the Capy route.")
        slug = p.slug if p else None
        scope = f"Route for {slug}" if slug else "Global route"
        routes = self.core.routes.list(self.name)
        route = next((r for r in routes if r.agent is None and r.project == slug), None)
        for r in routes:
            if r.target.get("captain"):
                place = r.project or "global"
                who = f" for {r.agent}" if r.agent else ""
                done.append(f"Capy route {place}{who}: {describe(r.target)}.")
        hint = choose.get("captain", "").strip().casefold()
        try:
            captains = [c for c in await self.api.list_captains() if c.get("id") and not c.get("archived")]
        except CapyError as e:
            if e.status in (401, 403):
                instruction = "Create a Capy API key that can see your Captains."
                if self.core.cfg.env.get(CAPY_KEY):
                    step = {"by": "person", "do": f"{instruction} Replace CAPY_API_KEY on the server and restart Switchboard."}
                else:
                    step = self.token_step(principal, CAPY_KEY, f"{instruction} Enter it through this private link.")
                return {"done": done, "next": [step]}
            raise BlockError("Capy could not list Captains. Try again.") from e
        done.extend(f"Available Captain: {c.get('name') or c.get('title') or c['id']} ({c['id']})." for c in captains)
        if hint:
            matches = [c for c in captains if hint == c["id"].casefold()] or [
                c for c in captains if hint in {str(c.get(k) or "").casefold() for k in ("code", "name", "title")}]
            if len(matches) != 1:
                raise BlockError("Choose the ID of exactly one available Captain for this route.")
            captain_id = matches[0]["id"]
        elif route and any(c["id"] == route.target.get("captain") for c in captains):
            captain_id = route.target["captain"]
        elif captains:
            args = {"what": kind, "choose": {"captain": captains[0]["id"] if len(captains) == 1 else "<Captain ID>"}}
            if slug:
                args["choose"]["project"] = slug
            return {"done": done, "next": [{"by": "you", "do": f"Confirm which available Captain should receive work on the {scope.lower()}, then call connect with its ID.",
                                            "call": {"tool": "connect", "arguments": args}}]}
        else:
            return {"done": done, "next": [{"by": "person", "do": "Make sure the Capy API key can see an active Captain for this route."}]}
        if not route or route.target.get("captain") != captain_id:
            routed = await self.core.set_route(principal, destination=self.name, agent=None, project=slug, target={"captain": captain_id},
                                               defaults=route.defaults if route and route.target.get("captain") else {"mode": "captain_thread"},
                                               allow_override=route.allow_override if route else False, note="Set by connect.")
            done.append(f"{scope} points to {describe(routed['target'])}.")
        if not self.chain(principal.source):
            if self.core.secret(self.send_key_env):
                step = {"by": "person", "do": "Restart Switchboard to load the saved Capy service key."}
            else:
                step = self.token_step(principal, self.send_key_env,
                                       "Create a read-write service user key in Capy Settings → API, then enter it through this private link.")
            return {"done": done, "next": [step]}
        return {"done": done, "next": []}

    async def start(self) -> None:
        if self.api is None:
            return
        self._follow_tasks["_keys"] = self.core.spawn(self._check_loop(), name="capy-key-check")
        for h in self.core.inbox.handles(destination=self.name, open_only=True, limit=500):
            if self.core.clock() - h["created_at"] > self.handle_ttl:
                await self._finish(h, "expired", "Switchboard stopped following this delegation after its time limit; read the thread with result.")
                continue
            self.follow(h["ref"])

    async def aclose(self) -> None:
        for t in list(self._follow_tasks.values()):
            t.cancel()
        if self._follow_tasks:
            await asyncio.gather(*self._follow_tasks.values(), return_exceptions=True)
        if self.api:
            await self.api.aclose()

    def need_api(self) -> CapyAPI:
        if self.api is None:
            raise DestinationError("Capy is unreachable. Run swb connect capy-captain to add an API key.")
        return self.api

    # Actions ---------------------------------------------------------------------------

    def actions(self) -> list[Action]:
        return [
            Action("delegate", "Delegate to Capy", "Hand a task to the routed Capy target.", self.delegate, capped=True, previewable=True),
            Action("status", "Capy thread status", "Status and latest messages of a Capy thread.", self.thread_status, read_only=True),
            Action("list", "List Capy work", "Delegations, a Captain's crew, or a project's threads.", self.list_work, read_only=True),
            Action("reply", "Reply to a Capy thread", "Send a follow-up into a thread.", self.reply),
            Action("result", "Capy thread result", "The latest final reply of a thread in full.", self.result, read_only=True),
            Action("captain_digest", "Captain digest", "A Captain's digest and crew asks.", self.captain_digest, read_only=True),
            Action("captains", "List Captains", "Captains the Capy key can see.", self.captains, read_only=True),
            Action("cancel", "Stop following", "Stop reporting on a delegation.", self.cancel, idempotent=True),
        ]

    async def _files(self, api: CapyAPI, files: list[dict[str, Any]]) -> list[dict[str, str]]:
        decoded, total = [], 0
        for f in files:
            name = str(f.get("name") or "").strip()
            if not name or "/" in name or len(name) > 200:
                raise DestinationError("each file needs a plain name like notes.md")
            if f.get("text") is not None:
                data, media = str(f["text"]).encode(), f.get("media_type") or "text/plain"
            elif f.get("base64"):
                try:
                    data = base64.b64decode(str(f["base64"]), validate=True)
                except ValueError as e:
                    raise DestinationError(f"file {name}: base64 is invalid") from e
                media = f.get("media_type") or "application/octet-stream"
            else:
                raise DestinationError(f"file {name}: pass text or base64")
            if not data:
                raise DestinationError(f"file {name} is empty")
            total += len(data)
            if total > self.max_file_bytes:
                raise DestinationError(f"files exceed {self.max_file_bytes} bytes in total")
            decoded.append((name, str(media), data))
        return [await api.upload(n, m, d) for n, m, d in decoded]

    def _allowed_repos(self, project: "Project | None", defaults: dict[str, Any]) -> set[str]:
        if project is not None and project.links.get("repos"):
            return {r.lower() for r in project.links["repos"]} | {r.lower() for r in defaults.get("repos") or []}
        out = {r.lower() for r in defaults.get("repos") or []}
        for p in self.core.projects.list():
            out |= {r.lower() for r in p.links.get("repos") or []}
        return out

    def plan(self, principal: "Principal", project: "Project | None", target: str | None) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Which target this caller's delegation goes to: (target, defaults, how it was chosen). Explicit targets need a route that allows it."""
        route, _ = self.core.routes.resolve(destination=self.name, agent=principal.source, project=project.slug if project else None)
        if target:
            if route is not None and not route.allow_override:
                raise BlockRefused(TARGET_REQUIRED, f"the {route.level} route sends this to {describe(route.target)} and does not allow overriding; "
                                   "leave target out, or have the owner set allow_override on that route")
            return parse_target(target), dict(route.defaults) if route else {}, "explicit"
        if route is None:
            raise DestinationError("No route is set for Capy. Set a route or pass a target.")
        return dict(route.target), dict(route.defaults), route.level

    async def delegate(self, call: Call, *, task: str, title: str | None = None, project: str | None = None, target: str | None = None,
                       repos: list[str] | None = None, files: list[dict[str, Any]] | None = None, model: str | None = None,
                       new_thread: bool | None = None) -> dict[str, Any]:
        task = (task or "").strip()
        if not task:
            raise DestinationError("task is empty")
        display_title = (call.handle_meta.get("display_subject") if "display_subject" in call.handle_meta
                         else display_subject(title, task, "")) or None
        if title:
            title = subject("", "", None, title, verb="") or None
        p = call.core.project(project)
        api = self.need_api()
        tgt, defaults, level = self.plan(call.principal, p, target)
        if level == "explicit":
            tgt, extra = await self.validate_route_target(tgt, {}, reveal=call.principal.is_memory_owner)
            defaults = {**defaults, **extra}
        call.note(target=describe(tgt), title=display_title, excerpt=task)
        repos = sorted({r.strip().lower() for r in [*(repos or []), *(defaults.get("repos") or [])] if r.strip()})
        allowed = self._allowed_repos(p, defaults)
        bad = [r for r in repos if r not in allowed]
        if bad:
            raise DestinationError(f"repo {bad[0]!r} is not linked to {'project ' + p.slug if p else 'any project'}; allowed: {sorted(allowed) or 'none linked'}")
        model = model or defaults.get("model")
        mode = defaults.get("mode") or ("captain_thread" if "captain" in tgt else "new_thread")
        if new_thread is not None and "captain" in tgt:
            mode = "new_thread" if new_thread else "captain_thread"

        project_id = tgt.get("project") or tgt.get("captain_project")
        in_thread = "thread" in tgt or ("captain" in tgt and mode == "captain_thread")
        if not in_thread and not project_id:
            raise DestinationError(f"{describe(tgt)} has no Capy project to start a thread in")
        send = self.sending(call)
        if call.dry_run:
            address = self.route_address(tgt)
            return {"to": str(address) if address else None, "project": p.slug if p else None, "route": level, "mode": mode}

        handle = call.handle or call.core.inbox.new_handle()
        body = [lead_with_subject(title, task) if title and in_thread else task]
        if repos:
            body += ["", "Repos: " + ", ".join(repos)]
        if p and p.links.get("capy_project"):
            body += [f"Capy project: {p.links['capy_project']}"]
        forward = f"|forward:{call.handle_meta['forward_of']}" if "forward_of" in call.handle_meta else ""
        same = hashlib.sha256(f"{call.principal.source}|{describe(tgt)}|{title or ''}|{task}{forward}".encode()).hexdigest()
        window = float(self.config.get("duplicate_window_s", 600))
        for h in call.core.inbox.handles(owner=call.principal.source, destination=self.name, limit=20):
            if h["meta"].get("same") == same and h["created_at"] >= call.core.clock() - window:
                call.note(ref=h["ref"], outcome="duplicate")
                return {"handle": h["handle"], "thread_id": h["ref"], "target": describe(tgt), "route": level, "mode": h["meta"].get("mode"),
                        "status": h["status"], "duplicate": True,
                        "next": f"You sent this exact task to {describe(tgt)} a moment ago, so this is that delegation's handle. "
                                f"Call wait(conversation='{h['handle']}'); use reply to add to it."}
        uploaded = await send(lambda a: self._files(a, files)) if files else []
        digest = hashlib.sha256(f"{call.principal.source}|{call.core.audit.today()}|{tgt}|{title or ''}|{task}{forward}".encode()).hexdigest()
        try:
            if in_thread:
                thread_id = tgt.get("thread") or tgt["captain"]
                cursor = await api.latest_cursor(thread_id)
                receipt = await send(lambda a: a.send_message(thread_id, "\n".join(body), files=uploaded or None, model=model,
                                                              delivery=defaults.get("delivery") or self.delivery, client_key=digest[:64]))
                meta: dict[str, Any] = {"mode": "message", "message_id": receipt.get("id"), "client_key": digest[:64], "deduped": bool(receipt.get("deduped"))}
                status = "sent"
            else:
                t = await send(lambda a: a.create_thread(request_id=str(uuid.UUID(digest[:32])), project_id=project_id, title=title, model=model,
                                                         message="\n".join(body) + ("\n\n(Attachments follow in the next message.)" if uploaded else ""),
                                                         captain_id=tgt.get("captain")))
                thread_id, cursor = t["id"], None
                if uploaded:
                    await send(lambda a: a.send_message(thread_id, "Attachments for the task above.", files=uploaded, delivery="steer", client_key=("files" + digest)[:64]))
                meta = {"mode": "new_thread", "project_id": project_id}
                status = t.get("status") or "working"
        except CapyError as e:
            raise DestinationError(f"Capy API refused the delegation: {e}") from e
        meta |= {"target": tgt, "route": level, "task": task[:300], "same": same, **call.handle_meta}
        meta.pop("display_subject", None)
        kind = next((k for k in TARGET_KINDS if tgt.get(k)), "thread")
        call.core.inbox.open_handle(handle=handle, owner=call.principal.source, destination=self.name, ref=thread_id,
                                    project=p.slug if p else None, title=display_title, meta=meta, status=status, stream_cursor=cursor,
                                    target=str(Address(self.name, kind, str(tgt[kind]) if tgt.get(kind) else thread_id)))
        call.note(ref=thread_id)
        self.follow(thread_id)
        return {
            "handle": handle,
            "thread_id": thread_id,
            "target": describe(tgt),
            "route": level,
            "mode": meta["mode"],
            "status": status,
            "next": "Call wait() for Capy's reply (the conversation is optional), or inbox later; status(conversation) shows progress.",
        }

    # Following threads ---------------------------------------------------------------

    def follow(self, thread_id: str) -> None:
        if self.api is None or thread_id in self._follow_tasks:
            return
        stream = ThreadStream(self.api.http, thread_id, lambda f: self._on_frame(thread_id, f), lambda: self._cursor(thread_id),
                              sleep=self.sleep, backoff=Backoff(base=self.backoff_base))
        self.followers[thread_id] = stream
        task = self.core.spawn(self._run(thread_id, stream), name=f"capy-follow-{thread_id}")
        self._follow_tasks[thread_id] = task

    def _open(self, thread_id: str) -> list[dict[str, Any]]:
        return [h for h in self.core.inbox.handles(destination=self.name, open_only=True, limit=500) if h["ref"] == thread_id]

    def _cursor(self, thread_id: str) -> str | None:
        cursors = [h["stream_cursor"] for h in self._open(thread_id)]
        if not cursors or any(c is None for c in cursors):
            return None
        return min(cursors)

    async def _run(self, thread_id: str, stream: ThreadStream) -> None:
        try:
            try:
                self._titles[thread_id] = str((await self.need_api().get_thread(thread_id)).get("title") or "")
            except CapyError:
                pass
            reason = await stream.run()
            if reason == "done":
                for h in self._open(thread_id):
                    await self._finish(h, "completed" if h["state"].get("last_reply") else "failed",
                                       h["state"].get("last_reply") or "The thread was archived before it replied.")
        except StreamClosed as e:
            for h in self._open(thread_id):
                if e.fatal:
                    await self._finish(h, "failed", f"Switchboard lost access to the thread: {e.reason}")
                else:
                    await self.core.inbox.add(h["handle"], "error", {"thread_id": thread_id, "text": f"Following the thread stopped: {e.reason}. It resumes on the next restart; status still works."})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("following %s failed", thread_id)
        finally:
            self.followers.pop(thread_id, None)
            self._follow_tasks.pop(thread_id, None)

    def _from(self, thread_id: str, entry: dict[str, Any] | None = None) -> dict[str, Any]:
        """Who an inbox item is from, taken only from Capy's own data: the thread, and the transcript entry's author."""
        out: dict[str, Any] = {"kind": "capy_thread", "thread_id": thread_id, "title": self._titles.get(thread_id) or None}
        if entry is not None:
            out["author"] = entry.get("authorName") or entry.get("source")
            out["message_id"] = entry.get("id")
            if entry.get("sender"):
                out["sender"] = entry["sender"]
        return out

    async def _finish(self, h: dict[str, Any], outcome: str, text: str) -> None:
        state = {**h["state"], "final": text[:RESULT_LIMIT]}
        self.core.inbox.update_handle(h["handle"], status=outcome, state=state, open=False)
        await self.core.inbox.add(h["handle"], "final", {"thread_id": h["ref"], "status": outcome, "text": text[:RESULT_LIMIT],
                                                         "pull_requests": state.get("pull_requests", []), "from": self._from(h["ref"])})

    @staticmethod
    def _rests(h: dict[str, Any]) -> tuple[str, ...]:
        return ("idle", "archived") if h["meta"].get("mode") == "new_thread" else ("idle", "waiting", "archived")

    @staticmethod
    def _owns(h: dict[str, Any]) -> bool:
        """Whether assistant output now belongs to this delegation. A new thread is entirely the delegation's. In a shared
        thread (a Captain), output belongs to the delegation whose message is the latest user entry, matched by the
        clientKey or message id Capy returned when it was sent, never by text the model wrote."""
        return h["meta"].get("mode") != "message" or bool(h["state"].get("current"))

    async def _on_frame(self, thread_id: str, frame: Frame) -> bool:
        handles = self._open(thread_id)
        if frame.event == "status":
            status = str(frame.data.get("status") or "")
            for h in handles:
                if status and status != h["status"]:
                    self.core.inbox.update_handle(h["handle"], status=status)
                    if status in ("waiting", "failed") and self._owns(h):
                        await self.core.inbox.add(h["handle"], "status", {"thread_id": thread_id, "status": status, "from": self._from(thread_id)})
                if h["state"].get("last_reply") and self._owns(h) and status in self._rests(h):
                    await self._finish(h, "completed", h["state"]["last_reply"])
            if status in ("idle", "waiting") and handles and self.api:
                await self._check_ask(thread_id)
        elif frame.event == "transcript":
            entry = frame.data
            for h in handles:
                cur = h["stream_cursor"]
                if frame.id and cur and frame.id <= cur:
                    continue
                state = dict(h["state"])
                if entry.get("source") == "user" and h["meta"].get("mode") == "message":
                    key = entry.get("clientKey")
                    mine = key == h["meta"].get("client_key") if key else bool(entry.get("id")) and entry.get("id") == h["meta"].get("message_id")
                    self.core.inbox.update_handle(h["handle"], state={**state, "current": mine}, stream_cursor=frame.id)
                elif entry.get("source") == "assistant" and entry.get("text") and self._owns(h):
                    text = str(entry["text"])
                    prs = sorted(set(state.get("pull_requests", [])) | set(pr_links([entry])))
                    state |= {"last_reply": text[:RESULT_LIMIT], "pull_requests": prs}
                    self.core.inbox.update_handle(h["handle"], state=state, stream_cursor=frame.id)
                    await self.core.inbox.add(h["handle"], "reply", {"thread_id": thread_id, "text": text[:RESULT_LIMIT], "pull_requests": prs,
                                                                     "attachments": [a["file"]["name"] for a in entry.get("attachments") or [] if a.get("file")],
                                                                     "from": self._from(thread_id, entry)})
                    if h["meta"].get("mode") == "message" and h["status"] in self._rests(h):
                        await self._finish({**h, "state": state}, "completed", text)
                elif frame.id:
                    self.core.inbox.update_handle(h["handle"], stream_cursor=frame.id)
        return not self._open(thread_id)

    async def _check_ask(self, thread_id: str) -> None:
        try:
            t = await self.need_api().get_thread(thread_id)
        except CapyError:
            return
        ask = (t.get("ask") or {}).get("text") if isinstance(t.get("ask"), dict) else None
        if not (t.get("needsYou") and ask):
            return
        for h in self._open(thread_id):
            if h["state"].get("ask") != ask and self._owns(h):
                self.core.inbox.update_handle(h["handle"], state={**h["state"], "ask": ask})
                await self.core.inbox.add(h["handle"], "ask", {"thread_id": thread_id, "text": ask, "from": self._from(thread_id)})

    async def cancel(self, call: Call, *, handle: str) -> dict[str, Any]:
        h = self.core.inbox.handle(handle)
        if h is None or h["destination"] != self.name or h["owner"] != call.principal.source:
            raise DestinationError(f"no Capy handle {handle!r}")
        if h["open"]:
            await self._finish(h, "cancelled", "Stopped following on request. The Capy thread itself keeps running; use reply to stop it.")
        call.note(ref=h["ref"])
        return {"handle": handle, "status": "cancelled" if h["open"] else h["status"], "thread_id": h["ref"]}

    # Reading Capy work ---------------------------------------------------------------

    def _routed(self) -> tuple[set[str], set[str], set[str]]:
        captains, projects, threads = set(), set(), set()
        for r in self.core.routes.list(self.name):
            if r.target.get("captain"):
                captains.add(r.target["captain"])
            if r.target.get("project"):
                projects.add(r.target["project"])
            if r.target.get("thread"):
                threads.add(r.target["thread"])
        for p in self.core.projects.list():
            if p.links.get("capy_project"):
                projects.add(p.links["capy_project"])
        return captains, projects, threads

    async def check_thread_allowed(self, thread_id: str) -> dict[str, Any]:
        """A thread is in reach when it was delegated here, or it belongs to a routed Captain, project, or thread."""
        t = await self.need_api().get_thread(thread_id)
        captains, projects, threads = self._routed()
        delegated = self.core.store.one("SELECT 1 FROM handles WHERE destination = ? AND ref = ?", (self.name, thread_id)) is not None
        if delegated or thread_id in captains | threads or (t.get("captain") or {}).get("jamId") in captains or t.get("projectId") in projects:
            return t
        raise BlockRefused(CONVERSATION_REQUIRED, "that thread is outside the Captains, projects and threads routed here")

    def _thread_ref(self, ref: str, call: Call, *, read: bool = False) -> str:
        if ref.startswith("h_"):
            h = self.core.inbox.handle(ref)
            if h is None or not (h["owner"] == call.principal.source or (read and call.principal.is_unrestricted_owner)):
                raise BlockRefused(CONVERSATION_REQUIRED, f"no handle {ref!r}")
            return h["ref"]
        return ref

    @staticmethod
    def _msg(m: dict[str, Any], limit: int = MSG_LIMIT) -> dict[str, Any]:
        text = m.get("text") or ""
        out = {"from": m.get("authorName") or m.get("source"), "source": m.get("source"), "at": m.get("createdAt"), "text": text[:limit] + (" …" if len(text) > limit else "")}
        if m.get("attachments"):
            out["attachments"] = [a["file"]["name"] for a in m["attachments"] if a.get("file")]
        return out

    async def thread_status(self, call: Call, *, thread_id: str, messages: int = 5, include_tools: bool = False) -> dict[str, Any]:
        api = self.need_api()
        thread_id = self._thread_ref(thread_id, call, read=True)
        t = await self.check_thread_allowed(thread_id)
        tail = await api.tail_messages(thread_id, limit=50)
        shown = [m for m in tail if include_tools or m.get("source") != "tool"][-max(1, min(messages, 20)):]
        return {**thread_summary(t), "pull_requests": pr_links(tail), "latest": [self._msg(m) for m in shown]}

    async def result(self, call: Call, *, thread_id: str, tail: int = 0) -> dict[str, Any]:
        api = self.need_api()
        thread_id = self._thread_ref(thread_id, call, read=True)
        t = await self.check_thread_allowed(thread_id)
        msgs = await api.tail_messages(thread_id, limit=60)
        final = next((m for m in reversed(msgs) if m.get("source") == "assistant"), None)
        out: dict[str, Any] = {**thread_summary(t), "pull_requests": pr_links(msgs)}
        if t.get("status") == "working":
            out["note"] = "The thread is still working; this is its latest reply so far."
        if final is None:
            out["final_reply"] = None
        else:
            out["final_reply"] = self._msg({**final, "text": final.get("text") or ""}, RESULT_LIMIT)
            atts = []
            for a in final.get("attachments") or []:
                f = a.get("file") or {}
                try:
                    d = await api.download_url(thread_id, f["fileId"])
                    atts.append({"name": f.get("name"), "media_type": f.get("mediaType"), "size": f.get("sizeBytes"), "title": a.get("title"), "url": d["url"], "expires_at": d.get("expiresAt")})
                except (CapyError, KeyError) as e:
                    atts.append({"name": f.get("name"), "error": str(e)})
            out["attachments"] = atts
        if tail:
            out["transcript_tail"] = [self._msg(m, 600) for m in msgs if m.get("source") != "tool"][-min(tail, 30):]
        return out

    async def reply(self, call: Call, *, thread_id: str, text: str, delivery: str = "steer") -> dict[str, Any]:
        api = self.need_api()
        if delivery not in DELIVERIES:
            raise DestinationError("delivery must be queue, steer or interrupt")
        if not (text or "").strip():
            raise DestinationError("text is empty")
        thread_id = self._thread_ref(thread_id, call)
        call.note(target=thread_id, ref=thread_id, excerpt=text)
        await self.check_thread_allowed(thread_id)
        send = self.sending(call)
        try:
            r = await send(lambda a: a.send_message(thread_id, text.strip(), delivery=delivery))
        except CapyError as e:
            raise DestinationError(f"Capy API refused the reply: {e}") from e
        return {"thread_id": thread_id, "message_id": r.get("id"), "delivery": delivery}

    def _default_captain(self, principal: "Principal", project: "Project | None" = None) -> str | None:
        route, _ = self.core.routes.resolve(destination=self.name, agent=principal.source, project=project.slug if project else None)
        if route and route.target.get("captain"):
            return route.target["captain"]
        captains = self._routed()[0]
        return next(iter(sorted(captains)), None)

    async def list_work(self, call: Call, *, scope: str = "delegated", project: str | None = None, captain: str | None = None, limit: int = 10) -> dict[str, Any]:
        api = self.need_api()
        limit = max(1, min(limit, 30))
        p = call.principal
        owner = None if p.is_unrestricted_owner else p.source
        if scope == "delegated":
            hs = self.core.inbox.handles(owner=owner, destination=self.name, project=project, scopes=p.scopes, limit=limit)

            async def one(h: dict[str, Any]) -> dict[str, Any]:
                base = {"handle": h["handle"], "thread_id": h["ref"], "by": h["owner"], "project": h["project"], "following": h["open"],
                        "handle_status": h["status"], "task": h["title"] or h["meta"].get("task", "")[:120]}
                try:
                    return {**base, **thread_summary(await api.get_thread(h["ref"]))}
                except CapyError as e:
                    return {**base, "error": str(e)}

            return {"scope": scope, "items": list(await asyncio.gather(*(one(h) for h in hs)))}
        visible = self.core.inbox.provider_refs(self.name, owner=owner, scopes=p.scopes) if owner is not None else None
        if scope == "captain":
            cid = (await self.resolve_captain(captain, reveal=call.principal.is_memory_owner))["id"] if captain else self._default_captain(call.principal, call.core.project(project))
            if not cid:
                raise DestinationError("no Captain is routed; pass captain")
            crew = [t for t in await api.captain_crew(cid) if visible is None or t["id"] in visible]
            crew.sort(key=lambda t: t.get("lastActivityAt") or t.get("updatedAt") or "", reverse=True)
            return {"scope": scope, "captain": cid, "items": [thread_summary(t) for t in crew[:limit]]}
        if scope == "project":
            entry = call.core.project(project)
            pid = entry.links.get("capy_project") if entry else None
            if not pid:
                raise DestinationError("project must be a switchboard project linked to a Capy project (links.capy_project)")
            if visible is None:
                page = await api.list_threads(pid, limit=limit)
                return {"scope": scope, "project": entry.slug, "items": [thread_summary(t) for t in page.get("items", [])]}
            if not p.allows_routing_scope(entry.scope):
                raise BlockRefused(SCOPE_REQUIRED)
            refs = list(dict.fromkeys(h["ref"] for h in self.core.inbox.handles(owner=owner, destination=self.name, project=entry.slug,
                                                                                scopes=p.scopes, limit=500)))[:limit]
            threads = await asyncio.gather(*(api.get_thread(ref) for ref in refs), return_exceptions=True)
            for t in threads:
                if isinstance(t, BaseException) and not isinstance(t, CapyError):
                    raise t
            return {"scope": scope, "project": entry.slug, "items": [thread_summary(t) for t in threads if not isinstance(t, BaseException)]}
        raise DestinationError("scope must be delegated, captain or project")

    async def autolink(self, principal: Principal, project: Project, *, force: bool = False) -> dict[str, Any]:
        """Link a switchboard project to its Capy project (an exact repo match beats a name match) and route it to that
        project's only Captain. Ambiguous or missing matches come back as candidates; existing links and routes stay unless force."""
        api = self.need_api()
        core = self.core
        out: dict[str, Any] = {"project": project.slug, "changed": [], "notes": [], "candidates": {}}
        capy_id = project.links.get("capy_project")
        if not capy_id or force:
            items = await api.list_projects()
            repos = set(project.links.get("repos") or [])
            by_repo = [c for c in items if repos & {str(r.get("repoFullName", "")).lower() for r in c.get("repos") or []}]
            names = {project.slug, project.name.strip().lower()}
            by_name = [c for c in items if {str(c.get("name", "")).strip().lower(), str(c.get("code", "")).strip().lower()} & names]
            pick, how = (by_repo, "repo") if by_repo else (by_name, "name")
            if len(pick) == 1:
                if pick[0]["id"] != capy_id:
                    core.projects.update(project.slug, links={"capy_project": pick[0]["id"]})
                    core.audit_auto(principal, self.name, "auto_link", project.slug, f"capy_project {pick[0]['id']} ({pick[0].get('name')}, by {how})")
                    out["changed"].append({"link": "capy_project", "value": pick[0]["id"], "name": pick[0].get("name"), "matched_by": how})
                    capy_id = pick[0]["id"]
            else:
                out["candidates"]["capy_project"] = [{"id": c["id"], "name": c.get("name"), "code": c.get("code"),
                                                      "repos": [r.get("repoFullName") for r in c.get("repos") or []]} for c in pick]
                out["notes"].append(f"{len(pick)} Capy projects match by {how}; ask the owner which one and set links.capy_project"
                                    if pick else "no Capy project matches its repos or name; ask the owner, or set links.capy_project")
        if not capy_id:
            out["notes"].append("not linked, so no route was added; the global route still applies")
            return out
        existing = self.core.store.one("SELECT 1 FROM routes WHERE destination = ? AND agent = '' AND project = ?", (self.name, project.slug))
        if existing and not force:
            out["notes"].append("a project route already exists; left as is (force=true replaces it)")
            return out
        captains = [c for c in await api.list_captains() if c.get("projectId") == capy_id and not c.get("archived")]
        if len(captains) != 1:
            out["candidates"]["captain"] = [{"id": c["id"], "code": c.get("code"), "name": c.get("name")} for c in captains]
            out["notes"].append(f"{len(captains)} Captains in that Capy project; set the route with routes_set" if captains
                                else "that Capy project has no Captain; the global route still applies")
            return out
        defaults = {"mode": "captain_thread", **({"repos": project.links["repos"]} if project.links.get("repos") else {})}
        target, defaults = await self.validate_route_target({"captain": captains[0]["id"]}, defaults)
        r = core.routes.set(destination=self.name, agent=None, project=project.slug, target=target, defaults=defaults,
                            allow_override=False, note="set automatically from the linked Capy project", by="auto")
        core.audit_auto(principal, self.name, "auto_route", f"*/{project.slug}", r.audit_excerpt())
        out["changed"].append({"route": r.as_dict()})
        return out

    async def resolve_captain(self, hint: str, *, reveal: bool = True, explicit_name: bool = False) -> dict[str, Any]:
        hint = hint.strip()
        captains = [c for c in await self.need_api().list_captains() if not c.get("archived")]
        exact = next((c for c in captains if (c.get("id") or "").lower() == hint.lower()), None)
        if exact is not None:
            return exact
        candidates = [{"kind": CAPTAIN_SLUG, "block": self.name, "id": c["id"],
                       "address": str(Address(self.name, CAPTAIN_SLUG, c["id"])),
                       "name": c.get("name") or c.get("title"),
                       "slugs": [_slug(c.get("name") or c.get("title") or ""), str(c.get("code") or "").lower()]}
                      for c in captains]
        slugs = self.core.slug_map(candidates)
        if explicit_name and hint.casefold() == CAPTAIN_SLUG:
            matches = [c for c in captains if hint.casefold() in {(c.get(k) or "").casefold() for k in ("code", "name", "title")}]
            if len(matches) == 1:
                return matches[0]
            raise CaptainTargetError(captain_selection_error(candidates, slugs, reveal=reveal))
        if target := slugs.get(hint.casefold()):
            return next(c for c in captains if c["id"] == target["id"])
        if hint.casefold() == CAPTAIN_SLUG:
            raise CaptainTargetError(captain_selection_error(candidates, slugs, reveal=reveal))
        for c in captains:
            names = {(c.get(k) or "").lower() for k in ("code", "name", "title")}
            if hint.lower() in names:
                return c
        raise DestinationError(f"no Captain matches {hint!r}; targets(block='capy') or capy_captains lists them")

    async def captains(self, call: Call) -> dict[str, Any]:
        routed = self._routed()[0]
        items = [{"id": c.get("id"), "name": c.get("name"), "title": c.get("title"), "description": (c.get("description") or "")[:200],
                  "project_id": c.get("projectId"), "status": c.get("status"), "crew": c.get("crewCount"), "needs_you": bool(c.get("needsYou") or c.get("crewNeedsYou")),
                  "routed": c.get("id") in routed} for c in await self.need_api().list_captains()]
        return {"captains": items}

    async def captain_digest(self, call: Call, *, captain: str | None = None, project: str | None = None) -> dict[str, Any]:
        api = self.need_api()
        cid = (await self.resolve_captain(captain, reveal=call.principal.is_memory_owner))["id"] if captain else self._default_captain(call.principal, call.core.project(project))
        if not cid:
            raise DestinationError("no Captain is routed; pass captain")
        d, crew = await asyncio.gather(api.captain_digest(cid), api.captain_crew(cid))
        md = d.get("markdown")
        return {"captain_id": cid, "file": d.get("path"), "updated_at": d.get("updatedAt"), "markdown": md[:60_000] if md else None,
                "crew_needs_you": [thread_summary(t) for t in crew if t.get("needsYou")],
                "crew_working": [thread_summary(t) for t in crew if t.get("status") == "working"][:15]}

    # Routes and context ------------------------------------------------------------

    async def validate_route_target(self, target: dict[str, Any], defaults: dict[str, Any], *, reveal: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
        kinds = [k for k in TARGET_KINDS if target.get(k)]
        if len(kinds) != 1:
            raise DestinationError("a Capy route target names exactly one of captain, project or thread")
        unknown = set(defaults) - DEFAULT_KEYS
        if unknown:
            raise DestinationError(f"unknown route defaults {sorted(unknown)}; allowed: {sorted(DEFAULT_KEYS)}")
        clean: dict[str, Any] = {}
        if defaults.get("model"):
            clean["model"] = str(defaults["model"])
        if defaults.get("repos"):
            clean["repos"] = sorted({str(r).strip().lower() for r in defaults["repos"] if str(r).strip()})
        if defaults.get("mode"):
            if defaults["mode"] not in MODES:
                raise DestinationError(f"mode must be one of {MODES}")
            clean["mode"] = defaults["mode"]
        if defaults.get("delivery"):
            if defaults["delivery"] not in DELIVERIES:
                raise DestinationError("delivery must be queue, steer or interrupt")
            clean["delivery"] = defaults["delivery"]
        api = self.need_api()
        kind, value = kinds[0], str(target[kinds[0]]).strip()
        try:
            if kind == "captain":
                c = await self.resolve_captain(value, reveal=reveal, explicit_name=target.get("explicit_name") is True)
                return {"captain": c["id"], "name": c.get("name") or c.get("title"), "captain_project": c.get("projectId")}, clean
            if kind == "project":
                if value.startswith("project_"):
                    pr = await api.get_project(value)
                else:
                    pr = next((x for x in await api.list_projects() if value.lower() in {(x.get("code") or "").lower(), (x.get("name") or "").lower()}), None)
                    if pr is None:
                        raise DestinationError(f"no Capy project matches {value!r}")
                return {"project": pr["id"], "name": pr.get("name") or pr.get("code")}, clean
            t = await api.get_thread(value)
            return {"thread": t["id"], "name": t.get("title"), "project_id": t.get("projectId")}, clean
        except CapyError as e:
            raise DestinationError(f"Capy could not resolve {kind} {value!r}: {e}") from e

    async def context_section(self, principal: "Principal", project: "Project | None") -> str | None:
        if self.api is None:
            return "## Capy\nCapy is not configured on this server (CAPY_API_KEY missing)."
        out = ["## Capy"]
        route, _ = self.core.routes.resolve(destination=self.name, agent=principal.source, project=project.slug if project else None)
        out.append(f"send goes to {describe(route.target)} ({route.level} route)." if route else "No route applies to you here; send needs to=<target>.")
        owner = None if principal.is_unrestricted_owner else principal.source
        open_handles = self.core.inbox.handles(owner=owner, destination=self.name, open_only=True,
                                               project=project.slug if project else None, scopes=principal.scopes, limit=10)
        if open_handles:
            out.append("\n### Delegations in progress")
            out += [f"- {h['title'] or h['meta'].get('task', '')[:80]} [{h['status']}] handle {h['handle']}, thread {h['ref']}" for h in open_handles]
        cid = route.target.get("captain") if route else None
        if cid and principal.is_unrestricted_owner:
            try:
                d, crew = await asyncio.gather(self.api.captain_digest(cid), self.api.captain_crew(cid))
            except CapyError as e:
                out.append(f"Could not read the Captain: {e}")
                return "\n".join(out)
            md = (d.get("markdown") or "").strip()
            if md:
                out.append(f"\n### Captain digest ({d.get('path') or 'digest'}, {d.get('updatedAt') or 'undated'})\n" + md[:DIGEST_LIMIT] + (" …" if len(md) > DIGEST_LIMIT else ""))
            pid = project.links.get("capy_project") if project else None
            relevant = [t for t in crew if not pid or t.get("projectId") == pid]
            asks = [t for t in relevant if t.get("needsYou")]
            working = [t for t in relevant if t.get("status") == "working"]
            out.append(f"\n### Captain crew{f' in {project.slug}' if pid else ''}\n{len(working)} working, {len(asks)} need the owner.")
            out += [f"- needs you: {(t.get('ask') or {}).get('text') or t.get('title')} (thread {t['id']})" for t in asks[:8]]
            out += [f"- working: {t.get('title')} (thread {t['id']})" for t in working[:8]]
        return "\n".join(out)

    # Block surface: targets, personas, verbs, tools, page -------------------------------------

    async def targets(self) -> list[dict[str, Any]]:
        """Captains, by name and code, each with a persona whose picture this block draws."""
        if self.api is None:
            return []
        out = []
        captains = [c for c in await self.api.list_captains() if not c.get("archived")]
        authors = {c["authorId"] for c in captains if isinstance(c.get("authorId"), str)}
        registrants = {}
        if authors:
            try:
                async with asyncio.timeout(4):
                    registrants = {
                        user["id"]: " ".join(
                            user[key].strip() for key in ("firstName", "lastName")
                            if isinstance(user.get(key), str) and user[key].strip()
                        )
                        for user in await self.api.list_users() if user.get("id") in authors
                    }
            except (CapyError, TimeoutError, httpx.HTTPError):
                pass
        persona_ids = set(self.core.known_agents())
        for c in captains:
            name = c.get("name") or c.get("title") or c["id"]
            slug = _slug(name)
            code = str(c.get("code") or "").lower()
            pid = slug or code or c["id"]
            if pid in persona_ids:
                pid = f"{pid[:29]}-{hashlib.sha256(c['id'].encode()).hexdigest()[:10]}"
            persona_ids.add(pid)
            out.append({"kind": "captain", "id": c["id"], "name": name, "slugs": [slug, code],
                        "created_by": registrants.get(c["authorId"]) or None if isinstance(c.get("authorId"), str) else None,
                        "created_at": _api_timestamp(c.get("createdAt")),
                        "last_seen_at": _api_timestamp(c.get("lastActivityAt")) or _api_timestamp(c.get("updatedAt")),
                        "persona": {"id": pid, "name": name, "avatar": c.get("avatar")}})
        return out

    async def render_avatar(self, persona: dict[str, Any], http: Any, out_dir: Any, remote: bool = True) -> str | None:
        from .avatars import render_captain

        return await render_captain(http, out_dir, persona.get("name") or persona["id"], persona.get("avatar"),
                                    remote and bool(self.config.get("avatars", True)), captain_id=persona.get("target_id"))

    def parse_route_target(self, text: str) -> dict[str, Any]:
        return parse_target(text)

    def verb_args(self, verb: str, ref: str, extra: dict[str, Any]) -> dict[str, Any]:
        if verb == "cancel":
            if not ref.startswith("h_"):
                raise DestinationError("cancel takes the conversation handle (h_...)")
            return {"handle": ref}
        allowed = {"reply": ("text", "delivery"), "status": ("messages", "include_tools"), "result": ("tail",)}[verb]
        return {"thread_id": ref, **{k: v for k, v in extra.items() if k in allowed}}

    def page_rows(self) -> list[tuple[str, str, str, bool | None]]:
        st = self.status()
        if not st.get("sends_as"):
            return []
        checks = st.get("send_keys") or {}

        def row(label: str, env: str | None) -> tuple[str, str, str, bool | None]:
            c = checks.get(env) or {"status": "unchecked", "detail": "not checked yet"}
            if not env:
                return (f"Sends as: {label}", "no key may send for this agent", "none", False)
            return (f"Sends as: {label}", f"{c['status']}: {c.get('detail', '')}", env, c["status"] == "ok")

        return [row("default", st["sends_as"]), *(row(a, env) for a, env in (st.get("sends_as_agents") or {}).items())]

    def mcp_tools(self, mcp: Any, kit: Any) -> None:
        from .mcp_tools import register

        register(self, mcp, kit)


CapyDestination = CapyBlock
