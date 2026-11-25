"""The Capy destination: delegate work to Capy threads and Captains, follow them over SSE, and report back into the inbox."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import uuid
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import httpx

from .. import Action, Call, Destination, DestinationError
from .api import CapyAPI, CapyError, pr_links, thread_summary
from .stream import Backoff, Frame, StreamClosed, ThreadStream

if TYPE_CHECKING:
    from ...auth import Principal
    from ...core import Switchboard
    from ...projects import Project

log = logging.getLogger("switchboard.capy")

MSG_LIMIT = 1500
RESULT_LIMIT = 20_000
DIGEST_LIMIT = 2500
DELIVERIES = ("queue", "steer", "interrupt")
MODES = ("captain_thread", "new_thread")
TARGET_KINDS = ("captain", "project", "thread")
DEFAULT_KEYS = {"model", "repos", "mode", "delivery"}
DONE_RE = re.compile(r"^\s*\W*switchboard:\s*done(?:\s+(h_[0-9a-f]+))?\W*\s*$", re.IGNORECASE | re.MULTILINE)


def parse_target(text: str) -> dict[str, str]:
    """'captain:<id|name>', 'project:<id|code|name>', 'thread:<jam_id>', or a bare jam_/project_ id or Captain name."""
    text = (text or "").strip()
    kind, sep, value = text.partition(":")
    if sep and kind.lower() in TARGET_KINDS and value.strip():
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


def strip_done(text: str) -> str:
    return DONE_RE.sub("", text).rstrip()


class CapyDestination(Destination):
    name = "capy"
    title = "Capy"

    def __init__(self, core: "Switchboard", config: dict[str, Any]):
        super().__init__(core, config)
        key = core.cfg.env.get("CAPY_API_KEY") or None
        base = str(config.get("api_base", "https://api.capy.ai/api/v1"))
        self.api: CapyAPI | None = CapyAPI(base, key, transport=config.get("transport")) if key else None
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
    def daily_cap(self) -> int | None:
        return int(self.config.get("daily_cap", 20))

    @property
    def online(self) -> bool:
        return self.api is not None

    def problems(self) -> list[str]:
        if self.api is None:
            return ["CAPY_API_KEY is not set: capy_* tools are disabled (memory, projects and routes still work)"]
        return []

    def status(self) -> dict[str, Any]:
        return {"following": sorted(self.followers), "open_handles": len(self.core.inbox.handles(destination=self.name, open_only=True, limit=500))}

    async def start(self) -> None:
        if self.api is None:
            return
        for h in self.core.inbox.handles(destination=self.name, open_only=True, limit=500):
            if self.core.clock() - h["created_at"] > self.handle_ttl:
                await self._finish(h, "expired", "Switchboard stopped following this delegation after its time limit; read the thread with capy_result.")
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
            raise DestinationError("Capy is not configured on this server (CAPY_API_KEY missing). Memory, project and route tools still work.")
        return self.api

    # Actions ---------------------------------------------------------------------------

    def actions(self) -> list[Action]:
        return [
            Action("delegate", "Delegate to Capy", "Hand a task to the routed Capy target.", self.delegate, capped=True),
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
        route, tried = self.core.routes.resolve(destination=self.name, agent=principal.source, project=project.slug if project else None)
        if target:
            if route is not None and not route.allow_override:
                raise DestinationError(f"the {route.level} route sends this to {describe(route.target)} and does not allow overriding; "
                                       "leave target out, or have the owner set allow_override on that route")
            return parse_target(target), dict(route.defaults) if route else {}, "explicit"
        if route is None:
            raise DestinationError(f"no Capy route for agent {principal.source!r}{f' and project {project.slug!r}' if project else ''} "
                                   f"(checked {', '.join(tried)}); set one with routes_set or pass target")
        return dict(route.target), dict(route.defaults), route.level

    async def delegate(self, call: Call, *, task: str, title: str | None = None, project: str | None = None, target: str | None = None,
                       repos: list[str] | None = None, files: list[dict[str, Any]] | None = None, model: str | None = None,
                       new_thread: bool | None = None) -> dict[str, Any]:
        api = self.need_api()
        task = (task or "").strip()
        if not task:
            raise DestinationError("task is empty")
        p = call.core.project(project)
        tgt, defaults, level = self.plan(call.principal, p, target)
        if level == "explicit":
            tgt, extra = await self.validate_route_target(tgt, {})
            defaults = {**defaults, **extra}
        call.note(target=describe(tgt), title=title, excerpt=task)
        repos = sorted({r.strip().lower() for r in [*(repos or []), *(defaults.get("repos") or [])] if r.strip()})
        allowed = self._allowed_repos(p, defaults)
        bad = [r for r in repos if r not in allowed]
        if bad:
            raise DestinationError(f"repo {bad[0]!r} is not linked to {'project ' + p.slug if p else 'any project'}; allowed: {sorted(allowed) or 'none linked'}")
        model = model or defaults.get("model")
        mode = defaults.get("mode") or ("captain_thread" if "captain" in tgt else "new_thread")
        if new_thread is not None and "captain" in tgt:
            mode = "new_thread" if new_thread else "captain_thread"

        handle = call.core.inbox.new_handle()
        header = f"[Delegated via switchboard from {call.principal.source}{f', project {p.slug}' if p else ''}]"
        body = [header, "", task]
        if repos:
            body += ["", "Repos: " + ", ".join(repos)]
        if p and p.links.get("capy_project"):
            body += [f"Capy project: {p.links['capy_project']}"]
        uploaded = await self._files(api, files) if files else []
        digest = hashlib.sha256(f"{call.principal.source}|{call.core.audit.today()}|{tgt}|{task}".encode()).hexdigest()
        try:
            if "thread" in tgt or ("captain" in tgt and mode == "captain_thread"):
                thread_id = tgt.get("thread") or tgt["captain"]
                body += ["", f"Switchboard handle {handle}: your replies here go back to {call.principal.source}. "
                             f"When this task is finished, end that reply with the line: switchboard: done {handle}"]
                cursor = await api.latest_cursor(thread_id)
                receipt = await api.send_message(thread_id, "\n".join(body), files=uploaded or None, model=model,
                                                 delivery=defaults.get("delivery") or self.delivery, client_key=digest[:64])
                meta: dict[str, Any] = {"mode": "message", "message_id": receipt.get("id"), "deduped": bool(receipt.get("deduped"))}
                status = "sent"
            else:
                project_id = tgt.get("project") or tgt.get("captain_project")
                if not project_id:
                    raise DestinationError(f"{describe(tgt)} has no Capy project to start a thread in")
                t = await api.create_thread(request_id=str(uuid.UUID(digest[:32])), project_id=project_id, title=title, model=model,
                                            message="\n".join(body) + ("\n\n(Attachments follow in the next message.)" if uploaded else ""),
                                            captain_id=tgt.get("captain"))
                thread_id, cursor = t["id"], None
                if uploaded:
                    await api.send_message(thread_id, "Attachments for the task above.", files=uploaded, delivery="steer", client_key=("files" + digest)[:64])
                meta = {"mode": "new_thread", "project_id": project_id}
                status = t.get("status") or "working"
        except CapyError as e:
            raise DestinationError(f"Capy API refused the delegation: {e}") from e
        meta |= {"target": tgt, "route": level, "task": task[:300]}
        call.core.inbox.open_handle(handle=handle, owner=call.principal.source, destination=self.name, ref=thread_id,
                                    project=p.slug if p else None, title=title, meta=meta, status=status, stream_cursor=cursor)
        call.note(ref=thread_id)
        self.follow(thread_id)
        return {
            "handle": handle,
            "thread_id": thread_id,
            "target": describe(tgt),
            "route": level,
            "mode": meta["mode"],
            "status": status,
            "next": f"Call capy_wait(handle='{handle}') to wait for Capy's reply, or capy_inbox later; capy_status(thread_id) shows progress.",
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
                    await self.core.inbox.add(h["handle"], "error", {"thread_id": thread_id, "text": f"Following the thread stopped: {e.reason}. It resumes on the next restart; capy_status still works."})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("following %s failed", thread_id)
        finally:
            self.followers.pop(thread_id, None)
            self._follow_tasks.pop(thread_id, None)

    async def _finish(self, h: dict[str, Any], outcome: str, text: str) -> None:
        state = {**h["state"], "final": text[:RESULT_LIMIT]}
        self.core.inbox.update_handle(h["handle"], status=outcome, state=state, open=False)
        await self.core.inbox.add(h["handle"], "final", {"thread_id": h["ref"], "status": outcome, "text": text[:RESULT_LIMIT],
                                                         "pull_requests": state.get("pull_requests", [])})

    async def _on_frame(self, thread_id: str, frame: Frame) -> bool:
        handles = self._open(thread_id)
        if frame.event == "status":
            status = str(frame.data.get("status") or "")
            for h in handles:
                if status and status != h["status"]:
                    self.core.inbox.update_handle(h["handle"], status=status)
                    if status in ("waiting", "failed"):
                        await self.core.inbox.add(h["handle"], "status", {"thread_id": thread_id, "status": status})
                if h["meta"].get("mode") == "new_thread" and h["state"].get("last_reply"):
                    if status == "idle":
                        await self._finish(h, "completed", h["state"]["last_reply"])
                    elif status == "archived":
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
                if entry.get("source") == "assistant" and entry.get("text"):
                    text = str(entry["text"])
                    done = [m.group(1) for m in DONE_RE.finditer(text)]
                    prs = sorted(set(state.get("pull_requests", [])) | set(pr_links([entry])))
                    state |= {"last_reply": strip_done(text)[:RESULT_LIMIT], "pull_requests": prs}
                    self.core.inbox.update_handle(h["handle"], state=state, stream_cursor=frame.id)
                    await self.core.inbox.add(h["handle"], "reply", {"thread_id": thread_id, "text": strip_done(text)[:RESULT_LIMIT], "pull_requests": prs,
                                                                     "attachments": [a["file"]["name"] for a in entry.get("attachments") or [] if a.get("file")]})
                    if done and (h["handle"] in done or (None in done and len(handles) == 1)):
                        await self._finish({**h, "state": state}, "completed", strip_done(text))
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
            if h["state"].get("ask") != ask:
                self.core.inbox.update_handle(h["handle"], state={**h["state"], "ask": ask})
                await self.core.inbox.add(h["handle"], "ask", {"thread_id": thread_id, "text": ask})

    async def cancel(self, call: Call, *, handle: str) -> dict[str, Any]:
        h = self.core.inbox.handle(handle)
        if h is None or h["destination"] != self.name:
            raise DestinationError(f"no Capy handle {handle!r}")
        if h["open"]:
            await self._finish(h, "cancelled", "Stopped following on request. The Capy thread itself keeps running; use capy_reply to stop it.")
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
        raise DestinationError("that thread is outside the Captains, projects and threads routed here")

    def _thread_ref(self, ref: str) -> str:
        if ref.startswith("h_"):
            h = self.core.inbox.handle(ref)
            if h is None:
                raise DestinationError(f"no handle {ref!r}")
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
        thread_id = self._thread_ref(thread_id)
        t = await self.check_thread_allowed(thread_id)
        tail = await api.tail_messages(thread_id, limit=50)
        shown = [m for m in tail if include_tools or m.get("source") != "tool"][-max(1, min(messages, 20)):]
        return {**thread_summary(t), "pull_requests": pr_links(tail), "latest": [self._msg(m) for m in shown]}

    async def result(self, call: Call, *, thread_id: str, tail: int = 0) -> dict[str, Any]:
        api = self.need_api()
        thread_id = self._thread_ref(thread_id)
        t = await self.check_thread_allowed(thread_id)
        msgs = await api.tail_messages(thread_id, limit=60)
        final = next((m for m in reversed(msgs) if m.get("source") == "assistant"), None)
        out: dict[str, Any] = {**thread_summary(t), "pull_requests": pr_links(msgs)}
        if t.get("status") == "working":
            out["note"] = "The thread is still working; this is its latest reply so far."
        if final is None:
            out["final_reply"] = None
        else:
            out["final_reply"] = self._msg({**final, "text": strip_done(final.get("text") or "")}, RESULT_LIMIT)
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
        thread_id = self._thread_ref(thread_id)
        call.note(target=thread_id, ref=thread_id, excerpt=text)
        await self.check_thread_allowed(thread_id)
        try:
            r = await api.send_message(thread_id, f"[Reply via switchboard from {call.principal.source}]\n\n{text.strip()}", delivery=delivery)
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
        if scope == "delegated":
            hs = self.core.inbox.handles(destination=self.name, project=project, limit=limit)

            async def one(h: dict[str, Any]) -> dict[str, Any]:
                base = {"handle": h["handle"], "thread_id": h["ref"], "by": h["owner"], "project": h["project"], "following": h["open"],
                        "handle_status": h["status"], "task": h["title"] or h["meta"].get("task", "")[:120]}
                try:
                    return {**base, **thread_summary(await api.get_thread(h["ref"]))}
                except CapyError as e:
                    return {**base, "error": str(e)}

            return {"scope": scope, "items": list(await asyncio.gather(*(one(h) for h in hs)))}
        if scope == "captain":
            cid = (await self.resolve_captain(captain))["id"] if captain else self._default_captain(call.principal, call.core.project(project))
            if not cid:
                raise DestinationError("no Captain is routed; pass captain")
            crew = await api.captain_crew(cid)
            crew.sort(key=lambda t: t.get("lastActivityAt") or t.get("updatedAt") or "", reverse=True)
            return {"scope": scope, "captain": cid, "items": [thread_summary(t) for t in crew[:limit]]}
        if scope == "project":
            p = call.core.project(project)
            pid = p.links.get("capy_project") if p else None
            if not pid:
                raise DestinationError("project must be a switchboard project linked to a Capy project (links.capy_project)")
            page = await api.list_threads(pid, limit=limit)
            return {"scope": scope, "project": p.slug, "items": [thread_summary(t) for t in page.get("items", [])]}
        raise DestinationError("scope must be delegated, captain or project")

    async def resolve_captain(self, hint: str) -> dict[str, Any]:
        hint = hint.strip()
        captains = await self.need_api().list_captains()
        for c in captains:
            names = {(c.get(k) or "").lower() for k in ("id", "code", "name", "title")}
            if hint.lower() in names:
                return c
        raise DestinationError(f"no Captain matches {hint!r}; capy_captains lists them")

    async def captains(self, call: Call) -> dict[str, Any]:
        routed = self._routed()[0]
        items = [{"id": c.get("id"), "name": c.get("name"), "title": c.get("title"), "description": (c.get("description") or "")[:200],
                  "project_id": c.get("projectId"), "status": c.get("status"), "crew": c.get("crewCount"), "needs_you": bool(c.get("needsYou") or c.get("crewNeedsYou")),
                  "routed": c.get("id") in routed} for c in await self.need_api().list_captains()]
        return {"captains": items}

    async def captain_digest(self, call: Call, *, captain: str | None = None, project: str | None = None) -> dict[str, Any]:
        api = self.need_api()
        cid = (await self.resolve_captain(captain))["id"] if captain else self._default_captain(call.principal, call.core.project(project))
        if not cid:
            raise DestinationError("no Captain is routed; pass captain")
        d, crew = await asyncio.gather(api.captain_digest(cid), api.captain_crew(cid))
        md = d.get("markdown")
        return {"captain_id": cid, "file": d.get("path"), "updated_at": d.get("updatedAt"), "markdown": md[:60_000] if md else None,
                "crew_needs_you": [thread_summary(t) for t in crew if t.get("needsYou")],
                "crew_working": [thread_summary(t) for t in crew if t.get("status") == "working"][:15]}

    # Routes and context ------------------------------------------------------------

    async def validate_route_target(self, target: dict[str, Any], defaults: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
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
                c = await self.resolve_captain(value)
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
        out.append(f"capy_delegate goes to {describe(route.target)} ({route.level} route)." if route else "No route applies to you here; capy_delegate needs a target.")
        open_handles = [h for h in self.core.inbox.handles(destination=self.name, open_only=True, project=project.slug if project else None, limit=10)]
        if open_handles:
            out.append("\n### Delegations in progress")
            out += [f"- {h['title'] or h['meta'].get('task', '')[:80]} [{h['status']}] handle {h['handle']}, thread {h['ref']}" for h in open_handles]
        cid = route.target.get("captain") if route else None
        if cid:
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
