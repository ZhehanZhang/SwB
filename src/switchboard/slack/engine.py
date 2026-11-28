from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx

from ..guard import RateLimiter
from ..subject import subject
from ..trust import frame_sender
from ..visibility import Visibility, Who
from .avatar_files import BUNDLED_AVATARS, AvatarFiles, find_avatar
from .config import Config, Persona
from .events import build_event, classify, event_id
from .push import WebhookSink
from .routing import Target, near, split_slug
from .slack import CHANNEL_ID_RE, BotPostIdentity, WorkspaceRuntime, make_client, slack_error
from .store import Store
from .writes import Actor, WriteError, WriteGate

log = logging.getLogger("switchboard.slack")

APPROVE_ACTION = "switchboard_approve"
REJECT_ACTION = "switchboard_reject"

EventListener = Callable[[dict[str, Any]], Awaitable[None]]


class SlackEngine:
    """Slack workspaces for switchboard: Socket Mode or polling intake, Web API reads and writes, personas, the write
    policy and approval gate, the bot-to-bot loop guard, and optional webhook sinks.

    Origins and destinations share one engine: the Slack origin listens to its events, the Slack destination calls its
    reads and writes. Auth is switchboard's: a caller is a switchboard agent, and config.grants says what it may do."""

    def __init__(
        self,
        config: Config,
        data_dir: str | Path,
        env: Mapping[str, str] | None = None,
        client_factory: Callable[[str], Any] = make_client,
        http: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.time,
        own_post_grace: float = 3.0,
        config_error: str | None = None,
        bot_identity: Callable[[str], BotPostIdentity] | None = None,
    ):
        self.env = dict(os.environ) if env is None else dict(env)
        self.data_dir = Path(data_dir)
        self.clock = clock
        self.store = Store(self.data_dir / "slack.db", clock=clock)
        self.http = http or httpx.AsyncClient()
        self.avatars = AvatarFiles(self.data_dir, config.public_base_url, config.avatar_dirs)
        self.client_factory = client_factory
        self.own_post_grace = own_post_grace
        self.config = config
        self.config_error = config_error
        self.bot_identity = bot_identity
        self.runtimes: dict[str, WorkspaceRuntime] = {}
        self.sinks: dict[str, list[WebhookSink]] = {}
        self.gate = WriteGate(self)
        self.senders = RateLimiter(clock=clock)
        self.senders.limits = {}
        self.listeners: list[EventListener] = []
        self.context_listeners: list[EventListener] = []
        self.context_limited: Callable[[dict[str, Any]], None] | None = None
        self.listen: Callable[[WorkspaceRuntime, dict[str, Any], dict[str, Any]], Awaitable[bool]] | None = None
        self.mode_intake: Callable[[WorkspaceRuntime, dict[str, Any]], Awaitable[tuple[bool, dict[str, Any] | None]]] | None = None
        self.mode_poll_channels: Callable[[WorkspaceRuntime], set[str]] | None = None
        self.relays: dict[tuple[str, str], Callable[[WorkspaceRuntime, dict[str, Any]], Awaitable[bool]]] = {}
        self.owner_channels: set[tuple[str, str]] = set()
        self.directory_listeners: list[Callable[[str, dict[str, Any]], Awaitable[None]]] = []
        self.visibility = Visibility(dict(config.visibility))
        self.targets: dict[str, Target] = {}
        self.dynamic_personas: dict[str, Persona] = {}
        self.target_agents: Callable[[Target], set[str]] = lambda t: set()
        self.connect = True
        self._tasks: list[asyncio.Task] = []

    @property
    def avatar_cache(self) -> Path:
        """Pictures blocks rendered for their dynamic personas (Captains, say), named by what they were rendered from."""
        return self.avatars.rendered

    def personas(self) -> dict[str, Persona]:
        """Configured personas, plus one per Captain target found automatically; the config wins on the same id."""
        return {**self.dynamic_personas, **self.config.personas}

    def set_targets(self, targets: dict[str, Target], personas: dict[str, Persona] | None = None) -> None:
        self.targets = targets
        if personas is not None:
            self.dynamic_personas = personas

    def thread_persona(self, workspace: str, channel: str | None, thread_ts: str | None, agent: str | None = None) -> str | None:
        """The persona of the target a thread is routed to; with agent, only if that agent is the one behind the target."""
        if not channel or not thread_ts:
            return None
        slug = self.store.thread_target(workspace, channel, thread_ts)
        target = self.targets.get(slug) if slug else None
        if target is None or (agent is not None and agent not in self.target_agents(target)):
            return None
        return target.persona

    def route(self, rt: WorkspaceRuntime, push: dict[str, Any], *, default_target: str | None = None) -> tuple[list[str], bool]:
        """Set the target and strip a leading slug. Return near matches and whether the sender named a target."""
        root = push["thread_ts"] or push["ts"]
        target: Target | None = None
        hint: list[str] = []
        explicit = False
        if push["addressed_to_us"]:
            word, rest = split_slug(push["text"], rt.bot_user_id)
            if word and word in self.targets:
                target, explicit = self.targets[word], True
                push["text"] = rest
            elif word and push["kind"] in ("mention", "dm"):
                hint = near(word, self.targets)
        sticky = self.store.thread_target(push["workspace"], push["channel"], root) if push["thread_ts"] else None
        slug = target.slug if target else (sticky or default_target or rt.cfg.default_target)
        if target is None and slug:
            target = self.targets.get(slug)
        push["target"] = slug
        push["target_kind"] = target.kind if target else None
        if slug and push["addressed_to_us"] and (explicit or not sticky):
            self.store.set_thread_target(push["workspace"], push["channel"], root, slug, push["target_kind"] or "")
        return hint, explicit

    def sink_accepts(self, label: str, push: dict[str, Any]) -> list[str]:
        return [s.cfg.name for s in self.sinks.get(label, []) if s.accepts(push)]

    def find_avatar(self, name: str) -> Path | None:
        return find_avatar(name, self.avatars.dirs)

    def avatar_url(self, name: str) -> str | None:
        return self.avatars.avatar_url(name)

    async def start(self, connect: bool = True) -> None:
        self.connect = connect
        await self._build()
        if connect:
            self._tasks.append(asyncio.create_task(self._maintenance(), name="maintenance"))

    async def _build(self) -> None:
        for label, ws in self.config.workspaces.items():
            rt = WorkspaceRuntime(self, ws, self.client_factory)
            self.runtimes[label] = rt
            self.sinks[label] = [WebhookSink(label, s, self.http) for s in ws.sinks]
            for sink in self.sinks[label]:
                sink.start()
            await rt.start(connect=self.connect)

    async def _teardown(self) -> None:
        for rt in self.runtimes.values():
            await rt.stop()
        for sinks in self.sinks.values():
            for sink in sinks:
                await sink.stop()
        self.runtimes.clear()
        self.sinks.clear()

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await self._teardown()
        await self.http.aclose()
        self.store.db.close()

    async def _maintenance(self) -> None:
        last_prune = 0.0
        while True:
            await asyncio.sleep(5)
            try:
                await self.gate.sweep()
                if self.clock() - last_prune > 3600:
                    self.store.prune_events(self.config.event_retention_days)
                    last_prune = self.clock()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("maintenance step failed")

    def runtime(self, label: str) -> WorkspaceRuntime:
        rt = self.runtimes.get(label)
        if rt is None:
            raise LookupError(f"unknown workspace {label!r}")
        return rt

    async def handle_socket(self, label: str, req_type: str, payload: dict[str, Any]) -> None:
        try:
            rt = self.runtime(label)
            if req_type == "events_api":
                if payload.get("team_id") and payload["team_id"] != rt.team_id:
                    log.warning("workspace %s: dropping event for team %s", label, payload["team_id"])
                    return
                event = payload.get("event") or {}
                if event.get("type") in ("user_change", "team_join"):
                    if (event.get("user") or {}).get("id") == rt.bot_user_id:
                        await rt.refresh_identity()
                    for listener in self.directory_listeners:
                        await listener(label, event)
                    return
                await self.ingest(label, event)
            elif req_type == "interactive" and payload.get("type") == "block_actions":
                await self.handle_block_actions(label, payload)
        except Exception:
            log.exception("workspace %s: failed to handle %s", label, req_type)

    async def ingest(self, label: str, event: dict[str, Any]) -> dict[str, Any] | None:
        """Let a relay consume the event; otherwise classify, dedupe, ack and fan it out."""
        rt = self.runtime(label)
        channel, ts = event.get("channel"), event.get("ts")
        if not channel:
            return None
        relay = self.relays.get((label, channel))
        if relay is not None and await relay(rt, event):
            return None
        if not ts:
            return None
        ctx = rt.classify_context()
        kind = classify(event, ctx)
        listen_only = kind is None
        listen_channels = rt.cfg.listen_channels if self.listen is not None else ()
        if listen_only and (self.mode_intake is not None or (listen_channels and rt.cfg.trust_of(event.get("user") or event.get("bot_id")) != "owner")):
            kind = classify(event, replace(ctx, watched=ctx.watched | {channel}))
        if kind is None:
            return None
        if rt.user_id and event.get("user") == rt.user_id and self.own_post_grace > 0:
            await asyncio.sleep(self.own_post_grace)
        if self.store.is_sent(rt.team_id, channel, ts):
            return None
        if self.mode_intake is None and listen_only and channel not in listen_channels and (
            all(CHANNEL_ID_RE.fullmatch(ref) for ref in listen_channels) or await rt.channel_name(channel) not in listen_channels
        ):
            return None
        channel_name = await rt.channel_name(channel)
        listening = channel in listen_channels or channel_name in listen_channels
        thread_ts = event.get("thread_ts")
        in_our_thread = bool(thread_ts and thread_ts != ts and ((rt.bot_user_id and event.get("parent_user_id") == rt.bot_user_id) or self.store.is_sent(rt.team_id, channel, thread_ts)))
        push = build_event(
            workspace=label,
            team=rt.team_id,
            event=event,
            kind=kind,
            channel_name=channel_name,
            user_name=None,
            team_url=rt.team_url,
            max_text_chars=self.config.max_text_chars,
            addressed_to_us=kind in ("mention", "dm") or in_our_thread,
        )
        candidate = {**push, "is_bot": bool(push["is_bot"] or event.get("is_bot") or event.get("subtype") == "bot_message")}
        if candidate["is_bot"] and event.get("bot_id") in rt.cfg.known and rt.cfg.trust_of(candidate["user"]) == "external":
            candidate["user"] = event["bot_id"]
        if self.mode_intake is not None:
            handled, routed = await self.mode_intake(rt, candidate)
            if handled:
                return routed
        if listen_only and not listening:
            return None
        if user_name := await rt.user_name(event.get("user")):
            candidate["user_name"] = push["user_name"] = user_name
        if listening and rt.cfg.trust_of(push["user"]) != "owner" and self.listen is not None:
            self.stamp(rt, candidate)
            if await self.listen(rt, candidate, push):
                return candidate
        if listen_only:
            return None
        hint, _ = self.route(rt, push)
        self.stamp(rt, push)
        if not self.store.add_event(push):
            return None
        if kind in ("mention", "dm") and rt.cfg.ack_reaction and "bot" in rt.clients:
            try:
                await rt.clients["bot"].reactions_add(channel=channel, timestamp=ts, name=rt.cfg.ack_reaction)
            except Exception as exc:
                log.info("ack reaction on %s failed: %s", event_id(rt.team_id, channel, ts), slack_error(exc))
        if hint and "bot" in rt.clients and push.get("user") and push["trust"] == "owner":
            try:
                await rt.clients["bot"].chat_postEphemeral(channel=channel, user=push["user"], thread_ts=push["thread_ts"] or ts,
                                                         text=f"No target by that name. Did you mean {', '.join(hint)}? Sent to {push['target'] or 'the default'} instead. "
                                                              f"Targets: {', '.join(sorted(self.targets)) or 'none yet'}.")
            except Exception as exc:
                log.info("slug hint on %s failed: %s", event_id(rt.team_id, channel, ts), slack_error(exc))
        if self.reaches(rt, push):
            await self.fan_out(label, push)
        else:
            await self.note_context(rt, push)
        return push

    def stamp(self, rt: WorkspaceRuntime, push: dict[str, Any]) -> None:
        """Set sender and trust from the Slack ids, and wrap a non-owner's text."""
        raw = push["text"]
        name = rt.cfg.known.get(push["user"] or "") or push["user_name"]
        channel = f"#{push['channel_name']}" if push.get("channel_name") else push["channel"]
        trust, push["text"] = frame_sender(raw, name, push["user"], f"in {push['workspace']} {channel}",
                                           owner=rt.cfg.trust_of(push["user"]) == "owner")
        push["trust"] = trust
        push["sender"] = {"id": push["user"], "name": name, "trust": trust, "is_bot": push["is_bot"]}
        push["subject"] = subject(name or push["user"] or "unknown", "Slack", channel, raw, trust=trust)

    def who(self, rt: WorkspaceRuntime, push: dict[str, Any]) -> Who:
        return Who(owner=push.get("trust") == "owner", id=push.get("user"), workspace=rt.label)

    def channel_resources(self, workspace: str, channel: str, name: str | None) -> list[str]:
        out = [f"channel:{workspace}/{channel}"]
        if name:
            out.append(f"channel:{workspace}/#{name}")
        return out

    def reaches(self, rt: WorkspaceRuntime, push: dict[str, Any]) -> bool:
        """Whether this sender may reach the message's channel and target. The owner always does; anyone else only
        where both are shared (or narrowed to include them)."""
        who = self.who(rt, push)
        if who.owner:
            return True
        if not self.visibility.allows(who, *self.channel_resources(rt.label, push["channel"], push.get("channel_name"))):
            return False
        return not push.get("target") or self.visibility.allows(who, *self.target_resources(push["target"]))

    def target_resources(self, slug: str) -> list[str]:
        """A target's visibility resources: the slug used, then its other slugs (an alias such as dot shares its rule)."""
        t = self.targets.get(slug)
        others = [s for s, x in self.targets.items() if t is not None and s != slug and x.kind == t.kind and x.id == t.id]
        return [f"target:{s}" for s in (slug, *sorted(others))]

    def channel_shared(self, workspace: str, channel: str) -> bool:
        rt = self.runtimes.get(workspace)
        name = rt.channel_names.get(channel) if rt else None
        return self.visibility.rule(*self.channel_resources(workspace, channel, name)) != "private"

    def sender_allowed(self, rt: WorkspaceRuntime, push: dict[str, Any], kind: str) -> bool:
        bucket, user = f"{kind}:{rt.label}", push["user"] or ""
        self.senders.limits[bucket] = rt.cfg.sender_rate
        if self.senders.blocked(bucket, user) is not None:
            self.store.audit(f"slack:{user or 'unknown'}", f"{kind}_rate_limited", rt.label,
                             message=push["id"], target=push.get("target"), trust=push["trust"])
            return False
        self.senders.hit(bucket, user)
        return True

    async def reply_place(self, workspace: str, channel: str, thread_ts: str | None, target: str | None, *,
                          trust: str | None = None) -> tuple[str, str | None, bool] | None:
        """Reply in the Slack thread. Without an owner message to reply to, a private target in a shared channel
        uses the owner's DM or withholds the reply."""
        if (trust == "owner" and thread_ts) or not target or self.visibility.rule(*self.target_resources(target)) != "private" or not self.channel_shared(workspace, channel):
            return channel, thread_ts, False
        owner = next(iter(sorted(self.config.workspaces[workspace].owner_user_ids)), None)
        rt = self.runtimes.get(workspace)
        if owner is None or rt is None or "bot" not in rt.clients:
            return None
        dm = await rt.clients["bot"].conversations_open(users=owner)
        return dm["channel"]["id"], None, True

    async def note_context(self, rt: WorkspaceRuntime, push: dict[str, Any]) -> None:
        """A non-owner's message to something private: filed as a context item in the target's inbox, never a task,
        a wake, a sink event or a reply. The sender gets a neutral ack."""
        label, actor = rt.label, f"slack:{push['user'] or 'unknown'}"
        detail = {"message": push["id"], "target": push.get("target"), "trust": push["trust"]}
        if not self.sender_allowed(rt, push, "context"):
            if push["addressed_to_us"] and self.context_limited is not None:
                self.context_limited(push)
            return
        push["context"] = True
        self.store.audit(actor, "context_noted", label, **detail)
        for listener in self.context_listeners:
            try:
                await listener(push)
            except Exception:
                log.exception("workspace %s: context listener failed", label)
        if push["addressed_to_us"]:
            await self.tell_sender(push, "Noted.")

    async def tell_sender(self, push: dict[str, Any], text: str) -> None:
        rt = self.runtimes.get(push["workspace"])
        if rt is None or "bot" not in rt.clients or not push.get("user") or push.get("is_bot"):
            return
        try:
            await rt.clients["bot"].chat_postEphemeral(channel=push["channel"], user=push["user"], thread_ts=push["thread_ts"] or push["ts"], text=text)
        except Exception as exc:
            log.info("note to sender on %s failed: %s", push["id"], slack_error(exc))

    async def fan_out(self, label: str, push: dict[str, Any], *, skip_delegate: bool = False, skip_agent: str | None = None) -> None:
        for sink in self.sinks.get(label, []):
            if sink.accepts(push):
                sink.put(push)
        event = {**push, "_skip_delegate": skip_delegate, "_skip_agent": skip_agent} if skip_delegate or skip_agent else push
        for listener in self.listeners:
            try:
                await listener(event)
            except Exception:
                log.exception("workspace %s: event listener failed", label)

    async def handle_block_actions(self, label: str, payload: dict[str, Any]) -> None:
        rt = self.runtime(label)
        user = (payload.get("user") or {}).get("id", "")
        team = (payload.get("team") or {}).get("id") or (payload.get("user") or {}).get("team_id")
        for action in payload.get("actions") or []:
            if action.get("action_id") not in (APPROVE_ACTION, REJECT_ACTION):
                continue
            rid = action.get("value", "")
            if (label, user) not in self.config.approvers() or team != rt.team_id:
                self.store.audit(f"slack:{user}", "decision_refused", label, request_id=rid, reason="wrong workspace")
                continue
            try:
                await self.gate.decide(rid, action["action_id"] == APPROVE_ACTION, Actor("slack", user, label))
            except WriteError as exc:
                await self._respond(payload, f"Not done: {exc}")

    async def _respond(self, payload: dict[str, Any], text: str) -> None:
        url = payload.get("response_url")
        if not url or not url.startswith("https://hooks.slack.com/"):
            return
        try:
            await self.http.post(url, json={"response_type": "ephemeral", "replace_original": False, "text": text}, timeout=10)
        except httpx.HTTPError:
            pass

    async def _approval_target(self, workspace: str | None = None) -> tuple[WorkspaceRuntime, str] | None:
        """The request's own workspace when it names an approver there, else approvals.notify_workspace."""
        ws = self.config.workspaces.get(workspace or "")
        if ws is not None and ws.approver_user_id:
            rt = self.runtimes.get(ws.label)
            if rt is not None and "bot" in rt.clients:
                return rt, ws.approver_user_id
        approvals = self.config.approvals
        if not approvals.notify_workspace or not approvals.approver_user_id:
            return None
        rt = self.runtimes.get(approvals.notify_workspace)
        if rt is None or "bot" not in rt.clients:
            return None
        return rt, approvals.approver_user_id

    def describe_request(self, req: dict[str, Any]) -> str:
        p = req["params"]
        ws = self.runtimes.get(req["workspace"])
        channel = p.get("channel", "")
        name = ws.channel_names.get(channel) if ws else None
        where = f"#{name} ({channel})" if name else channel
        lines = [
            f"*Write request* {req['id']} from agent {req['key_id']}",
            f"Workspace: {req['workspace']}   Identity: {req['identity']}   Action: {req['action']}",
            f"Target: {where}" + (f"   Thread: {p['thread_ts']}" if p.get("thread_ts") else ""),
        ]
        if req.get("persona"):
            lines.append(f"Persona: {req['persona']['name']}")
        if req["action"] == "add_reaction":
            lines.append(f"Reaction :{p['name']}: on message {p['ts']}")
        return "\n".join(lines)

    async def dm_owner(self, text: str) -> bool:
        """A plain DM to the owner (the approver), for switchboard notices such as a paused conversation."""
        target = await self._approval_target()
        if target is None:
            return False
        rt, approver = target
        try:
            dm = await rt.clients["bot"].conversations_open(users=approver)
            await rt.clients["bot"].chat_postMessage(channel=dm["channel"]["id"], text=text, **rt.bot_identity_overrides())
        except Exception as e:
            log.warning("owner DM failed: %s", type(e).__name__)
            return False
        return True

    async def notify_request(self, req: dict[str, Any]) -> None:
        target = await self._approval_target(req["workspace"])
        if target is None:
            return
        rt, approver = target
        bot = rt.clients["bot"]
        dm = await bot.conversations_open(users=approver)
        dm_channel = dm["channel"]["id"]
        text = req["params"].get("text", "")
        blocks: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": self.describe_request(req)}}]
        if text:
            blocks.append({"type": "section", "text": {"type": "plain_text", "text": text[:2900]}})
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {"type": "button", "style": "primary", "text": {"type": "plain_text", "text": "Approve"}, "action_id": APPROVE_ACTION, "value": req["id"]},
                    {"type": "button", "style": "danger", "text": {"type": "plain_text", "text": "Reject"}, "action_id": REJECT_ACTION, "value": req["id"]},
                ],
            }
        )
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"Expires <!date^{int(req['expires_at'])}^{{date_short_pretty}} {{time}}|soon>"}]})
        resp = await bot.chat_postMessage(channel=dm_channel, text=f"Write request {req['id']} needs your approval",
                                          blocks=blocks, **rt.bot_identity_overrides())
        self.store.set_request_notice(req["id"], {"workspace": rt.label, "channel": resp["channel"], "ts": resp["ts"]})

    async def close_notice(self, req: dict[str, Any]) -> None:
        notice = req.get("notice")
        if not notice:
            return
        rt = self.runtimes.get(notice["workspace"])
        if rt is None or "bot" not in rt.clients:
            return
        outcome = f"*{req['status'].upper()}* by {req.get('decided_by') or 'system'}"
        if req["status"] == "sent" and req.get("result", {}).get("permalink"):
            outcome += f" - <{req['result']['permalink']}|view>"
        if req["status"] == "failed" and req.get("result"):
            outcome += f" - {req['result'].get('error')}"
        await rt.clients["bot"].chat_update(
            channel=notice["channel"],
            ts=notice["ts"],
            text=f"Write request {req['id']}: {req['status']}",
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": self.describe_request(req)}},
                {"type": "section", "text": {"type": "plain_text", "text": (req["params"].get("text") or "-")[:2900]}},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": outcome}]},
            ],
        )

    def persona_rows(self) -> list[dict[str, Any]]:
        """Each persona with its picture's URL, the folder it comes from, and the agents that post as it by default."""
        cfg = self.config
        sources = {BUNDLED_AVATARS: "bundled mark", self.avatar_cache: "rendered by a block"}
        rows = []
        for pid, p in self.personas().items():
            path = self.find_avatar(p.avatar) if p.avatar else None
            url = (self.avatar_url(p.avatar) if p.avatar else None) or p.icon_url
            used = sorted({g.id for g in cfg.grants.values() if pid in (g.persona, g.id, *g.persona_by_workspace.values())}
                          | {f"default in {w.label}" for w in cfg.workspaces.values() if w.default_persona == pid}
                          | {f"target {t.slug}" for t in self.targets.values() if t.persona == pid})
            rows.append({"id": pid, "name": p.name, "avatar": p.avatar, "url": url, "avatar_url": url,
                         "emoji": p.emoji, "source": (sources.get(path.parent, "avatar folder") if path else ("icon_url" if p.icon_url else "none")),
                         "used_by": used})
        return rows

    def status(self) -> dict[str, Any]:
        workspaces = {label: rt.status() for label, rt in self.runtimes.items()}
        problems = []
        if self.config_error:
            problems.append(f"config error: {self.config_error}")
        if not self.config.workspaces:
            problems.append("slack: no workspaces configured")
        for label, st in workspaces.items():
            if st["missing_env"]:
                problems.append(f"{label}: missing {', '.join(st['missing_env'])}")
            if st["errors"]:
                problems.append(f"{label}: {st['errors'][-1]}")
        return {
            "status": "degraded" if problems else "ok",
            "problems": problems,
            "workspaces": workspaces,
            "sinks": {label: [s.status() for s in sinks] for label, sinks in self.sinks.items()},
            "agents": sorted(self.config.grants),
            "pending_approvals": len(self.store.list_requests(status="pending", limit=500)),
        }
