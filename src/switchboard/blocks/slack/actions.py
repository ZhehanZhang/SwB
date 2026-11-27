"""The Slack block's out side: reads and writes in the owner's Slack workspaces, through the shared Slack engine.

A caller is a switchboard agent (OAuth agent or bearer key name), and
slack.agents.<agent> says which workspaces and identities it may use. Writes follow each identity's policy: free,
approval (queued until the owner approves out of band: the Slack DM buttons or the switchboard page), or never.
No caller of this destination can approve a write.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from .. import Action, Block, BlockError, Call
from ...slack.config import Grant, WorkspaceConfig
from ...slack.personas import USER_POST_NOTE, PersonaError
from ...slack.slack import WorkspaceRuntime, slack_error
from ...slack.writes import WriteError

if TYPE_CHECKING:
    from ...slack.engine import SlackEngine

DestinationError = BlockError

REQUEST_FIELDS = ("id", "workspace", "identity", "action", "params", "status", "created_at", "expires_at", "decided_at", "result")


class SlackActions(Block):
    engine: "SlackEngine | None" = None

    @property
    def online(self) -> bool:
        return self.engine is not None and any(rt.clients for rt in self.engine.runtimes.values())

    def problems(self) -> list[str]:
        if self.engine is None:
            return ["the slack block is configured but has no Slack settings (workspaces)"]
        return list(self.engine.status()["problems"])

    def status(self) -> dict[str, Any]:
        if self.engine is None:
            return {}
        st = self.engine.status()
        return {"workspaces": st["workspaces"], "agents": st["agents"], "pending_approvals": st["pending_approvals"],
                "sinks": {label: [s.get("name") for s in sinks] for label, sinks in st["sinks"].items()}}

    def need(self) -> "SlackEngine":
        if self.engine is None:
            raise DestinationError("Slack is not configured on this server (no slack section)")
        return self.engine

    def grant(self, call: Call) -> Grant:
        g = self.need().config.grant(call.principal.source)
        if g is None:
            raise DestinationError(f"agent {call.principal.source!r} has no Slack access; the owner adds it under slack.agents.{call.principal.source}")
        return g

    def scope(self, g: Grant, workspace: str, identity: str | None = None) -> tuple[WorkspaceConfig, WorkspaceRuntime, str]:
        engine = self.need()
        ws = engine.config.workspaces.get(workspace)
        allowed = g.identities_for(workspace)
        if ws is None or not allowed:
            raise DestinationError(f"unknown workspace {workspace!r} for {g.id}; see slack_workspaces")
        if identity:
            if identity not in allowed:
                raise DestinationError(f"{g.id} may not use identity {identity!r} in {workspace!r} (allowed: {list(allowed)})")
        else:
            identity = "bot" if "bot" in allowed else allowed[0]
        rt = engine.runtimes.get(workspace)
        if rt is None or identity not in rt.clients:
            raise DestinationError(f"{workspace!r} has no working {identity} token (see switchboard_status)")
        return ws, rt, identity

    @staticmethod
    async def slack(coro: Any) -> Any:
        try:
            return await coro
        except DestinationError:
            raise
        except LookupError as exc:
            raise DestinationError(str(exc)) from exc
        except Exception as exc:
            raise DestinationError(f"slack error: {slack_error(exc)}") from exc

    async def compact(self, rt: WorkspaceRuntime, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        users = {m.get("user") for m in messages if m.get("user")}
        names = dict(zip(users, await asyncio.gather(*(rt.user_name(u) for u in users))))
        limit = self.need().config.max_text_chars
        out = []
        for m in messages:
            item = {"ts": m.get("ts"), "user": m.get("user") or m.get("bot_id"), "user_name": names.get(m.get("user")) or m.get("username"),
                    "text": (m.get("text") or "")[:limit]}
            for k in ("thread_ts", "reply_count", "subtype", "bot_id"):
                if m.get(k):
                    item[k] = m[k]
            if m.get("files"):
                item["files"] = [f.get("name") for f in m["files"]]
            if m.get("reactions"):
                item["reactions"] = {r["name"]: r.get("count", 0) for r in m["reactions"]}
            out.append(item)
        return out

    def actions(self) -> list[Action]:
        return [
            Action("me", "Slack access", "Your Slack workspaces, identities and persona.", self.me, read_only=True),
            Action("workspaces", "Slack workspaces", "Workspaces you may use, with identities, write policies and connection status.", self.workspaces, read_only=True),
            Action("channels", "Slack channels", "Channels visible to an identity.", self.channels, read_only=True),
            Action("search", "Search Slack", "Search messages (user identity).", self.search, read_only=True),
            Action("history", "Slack channel history", "Recent messages in a channel.", self.history, read_only=True),
            Action("thread", "Slack thread", "A thread's parent and replies.", self.thread, read_only=True),
            Action("watched", "Watched Slack channels", "Channels whose new messages reach your inbox.", self.watched, read_only=True),
            Action("events", "Recent Slack events", "Recent watched, mention and DM events.", self.events, read_only=True),
            Action("personas", "Slack personas", "Personas for bot posts.", self.persona_list, read_only=True),
            Action("targets", "Slack targets", "Slugs that route a Slack mention to a target.", self.slug_targets, read_only=True),
            Action("post", "Post to Slack", "Post a message or thread reply.", self.post),
            Action("react", "React in Slack", "Add an emoji reaction.", self.react),
            Action("request_write", "Ask to post to Slack", "Queue a post for the owner's approval.", self.request_write),
            Action("write_request", "Slack write request", "One of your write requests.", self.write_request, read_only=True),
            Action("write_requests", "Slack write requests", "Your write requests.", self.write_requests, read_only=True),
            Action("reply", "Reply in Slack", "Answer the latest message addressed to you on a Slack conversation, in its thread.", self.reply),
        ]

    async def me(self, call: Call) -> dict[str, Any]:
        g = self.grant(call)
        return {"agent": g.id, "workspaces": {w: list(ids) for w, ids in g.scopes.items()}, "persona": g.persona,
                "may_choose_persona": g.allow_persona_override, "allowed_personas": list(g.allowed_personas) if g.allowed_personas is not None else None}

    async def workspaces(self, call: Call) -> list[dict[str, Any]]:
        g, engine = self.grant(call), self.need()
        out = []
        for label, ids in g.scopes.items():
            ws, rt = engine.config.workspaces[label], engine.runtimes.get(label)
            out.append({"workspace": label, "team_id": ws.team_id, "user_only": ws.user_only,
                        "identities": {i: {"write": ws.identities[i].write, "ready": bool(rt and i in rt.clients)} for i in ids},
                        "bot_user_id": rt.bot_user_id if rt else None, "owner_user_id": rt.user_id if rt else None,
                        "intake": ws.intake, "connected": bool(rt and rt.connected)})
        return out

    async def channels(self, call: Call, *, workspace: str, query: str = "", identity: str = "", include_private: bool = True, limit: int = 200) -> list[dict[str, Any]]:
        _, rt, ident = self.scope(self.grant(call), workspace, identity or None)
        client = rt.client(ident)
        types = "public_channel,private_channel" if include_private else "public_channel"
        found: list[dict[str, Any]] = []
        cursor = None
        while len(found) < limit:
            resp = await self.slack(client.conversations_list(types=types, exclude_archived=True, limit=500, cursor=cursor))
            for ch in resp.get("channels", []):
                rt.channel_names[ch["id"]] = ch.get("name", ch["id"])
                if query.lower() in (ch.get("name") or "").lower():
                    found.append({"id": ch["id"], "name": ch.get("name"), "private": ch.get("is_private", False), "member": ch.get("is_member"),
                                  "members": ch.get("num_members"), "topic": (ch.get("topic") or {}).get("value") or None, "watched": ch["id"] in rt.watched_ids})
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return found[:limit]

    async def search(self, call: Call, *, workspace: str, query: str, count: int = 20) -> list[dict[str, Any]]:
        _, rt, ident = self.scope(self.grant(call), workspace, "user")
        resp = await self.slack(rt.client(ident).search_messages(query=query, count=max(1, min(count, 100))))
        limit = self.need().config.max_text_chars
        return [{"channel": (m.get("channel") or {}).get("id"), "channel_name": (m.get("channel") or {}).get("name"), "ts": m.get("ts"),
                 "user": m.get("user"), "user_name": m.get("username"), "text": (m.get("text") or "")[:limit], "permalink": m.get("permalink")}
                for m in (resp.get("messages") or {}).get("matches", [])]

    async def history(self, call: Call, *, workspace: str, channel: str, limit: int = 30, oldest: str = "", latest: str = "", cursor: str = "", identity: str = "") -> dict[str, Any]:
        _, rt, ident = self.scope(self.grant(call), workspace, identity or None)
        channel_id = await self.slack(rt.resolve_channel(channel))
        args: dict[str, Any] = {"channel": channel_id, "limit": max(1, min(limit, 200))}
        for k, v in (("oldest", oldest), ("latest", latest), ("cursor", cursor)):
            if v:
                args[k] = v
        resp = await self.slack(rt.client(ident).conversations_history(**args))
        return {"channel": channel_id, "channel_name": await rt.channel_name(channel_id), "messages": await self.compact(rt, resp.get("messages", [])),
                "next_cursor": (resp.get("response_metadata") or {}).get("next_cursor") or None}

    async def thread(self, call: Call, *, workspace: str, channel: str, thread_ts: str, limit: int = 100, identity: str = "") -> dict[str, Any]:
        _, rt, ident = self.scope(self.grant(call), workspace, identity or None)
        channel_id = await self.slack(rt.resolve_channel(channel))
        resp = await self.slack(rt.client(ident).conversations_replies(channel=channel_id, ts=thread_ts, limit=max(1, min(limit, 200))))
        return {"channel": channel_id, "thread_ts": thread_ts, "messages": await self.compact(rt, resp.get("messages", []))}

    async def watched(self, call: Call, *, workspace: str = "") -> dict[str, Any]:
        g, engine = self.grant(call), self.need()
        out: dict[str, Any] = {}
        for label in [workspace] if workspace else list(g.scopes):
            if label not in g.scopes or label not in engine.runtimes:
                raise DestinationError(f"unknown workspace {label!r} for {g.id}")
            rt = engine.runtimes[label]
            out[label] = {"channels": [{"id": c, "name": rt.channel_names.get(c)} for c in sorted(rt.watched_ids)], "unresolved": rt.unresolved_watched}
        return out

    async def events(self, call: Call, *, workspace: str = "", kind: str = "", since: float = 0, limit: int = 50) -> list[dict[str, Any]]:
        g = self.grant(call)
        labels = [workspace] if workspace else list(g.scopes)
        if any(label not in g.scopes for label in labels):
            raise DestinationError(f"unknown workspace {workspace!r} for {g.id}")
        return self.need().store.recent_events(labels, kind or None, since or None, limit)

    async def slug_targets(self, call: Call, refresh: bool = False) -> dict[str, Any]:
        self.grant(call)
        return await self.refresh() if refresh else self.describe()  # type: ignore[attr-defined]

    async def persona_list(self, call: Call) -> dict[str, Any]:
        g, personas = self.grant(call), self.need().personas()
        if not g.allow_persona_override:
            visible = [g.persona] if g.persona else []
        elif g.allowed_personas is not None:
            visible = list(g.allowed_personas)
        else:
            visible = list(personas)
        return {"default_for_agent": g.persona, "may_choose": g.allow_persona_override, "personas": [asdict(personas[p]) for p in visible if p in personas],
                "note": USER_POST_NOTE}

    async def _write(self, call: Call, workspace: str, identity: str, action: str, params: dict[str, Any], persona: str = "", force: bool = False) -> dict[str, Any]:
        g = self.grant(call)
        _, rt, ident = self.scope(g, workspace, identity or None)
        params["channel"] = await self.slack(rt.resolve_channel(params["channel"]))
        call.note(target=f"{workspace}/{params['channel']}" + (f"/{params['thread_ts']}" if params.get("thread_ts") else ""), excerpt=params.get("text") or params.get("name") or "")
        from .intake import slack_requester

        try:
            out = await self.need().gate.submit(g, workspace, ident, action, params, persona or None, force_approval=force,
                                                requester=slack_requester(call.principal))
        except (WriteError, PersonaError) as exc:
            raise DestinationError(str(exc)) from exc
        call.note(ref=out.get("request_id") or out.get("ts"), detail=f"as {ident}: {out['status']}", outcome="queued" if out["status"] == "pending_approval" else None)
        return out

    async def post(self, call: Call, *, workspace: str, channel: str, text: str, identity: str = "", thread_ts: str = "", persona: str = "", reply_broadcast: bool = False) -> dict[str, Any]:
        if not text.strip():
            raise DestinationError("text is empty")
        return await self._write(call, workspace, identity, "post_message", {"channel": channel, "text": text, "thread_ts": thread_ts or None, "reply_broadcast": reply_broadcast}, persona)

    async def reply(self, call: Call, *, ref: str, text: str, **_: Any) -> dict[str, Any]:
        """reply on a Slack conversation (the inbox handle a workspace's events arrive on): posted in the thread of the
        latest message there that was addressed to you, as your persona. Without an owner message, a private target's
        answer in a shared channel goes to the owner's DM."""
        h = self.core.inbox.handle(ref)
        if h is None or h["owner"] != call.principal.source:
            raise DestinationError(f"no conversation {ref!r}")
        src: dict[str, Any] | None = None
        target = None
        for row in self.core.store.q("SELECT data FROM inbox WHERE handle = ? AND kind = 'slack_event' ORDER BY id DESC LIMIT 50", (ref,)):
            d = json.loads(row["data"])
            if d.get("addressed_to_us"):
                src, target = d["from"], d.get("target")
                break
        if src is None:
            raise DestinationError("nothing on this conversation was addressed to you; use slack_post with a channel and thread_ts")
        place = await self.need().reply_place(src["workspace"], src["channel"], src.get("thread_ts") or src["ts"], target, trust=src.get("trust"))
        if place is None:
            raise DestinationError("this is a private target in a shared channel and the owner's DM can't be opened")
        channel, thread_ts, _dm = place
        out = await self.post(call, workspace=src["workspace"], channel=channel, text=text, thread_ts=thread_ts or "")
        return {**out, "conversation": ref, "posted_in": channel, "thread_ts": thread_ts}

    async def react(self, call: Call, *, workspace: str, channel: str, ts: str, name: str, identity: str = "") -> dict[str, Any]:
        return await self._write(call, workspace, identity, "add_reaction", {"channel": channel, "ts": ts, "name": name.strip(":")})

    async def request_write(self, call: Call, *, workspace: str, channel: str, text: str, identity: str = "", thread_ts: str = "", persona: str = "") -> dict[str, Any]:
        return await self._write(call, workspace, identity, "post_message", {"channel": channel, "text": text, "thread_ts": thread_ts or None, "reply_broadcast": False}, persona, force=True)

    async def write_request(self, call: Call, *, request_id: str) -> dict[str, Any]:
        g = self.grant(call)
        req = self.need().store.get_request(request_id)
        if req is None or req["key_id"] != g.id:
            raise DestinationError(f"no write request {request_id} for {g.id}")
        return {k: req[k] for k in REQUEST_FIELDS}

    async def write_requests(self, call: Call, *, status: str = "", limit: int = 20) -> list[dict[str, Any]]:
        g = self.grant(call)
        reqs = self.need().store.list_requests(status=status or None, key_id=g.id, limit=limit)
        return [{k: r[k] for k in ("id", "workspace", "identity", "action", "status", "created_at", "expires_at")} for r in reqs]
