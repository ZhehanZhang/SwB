from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

import httpx

from .config import Config
from .events import build_event, classify, event_id
from .push import WebhookSink
from .slack import WorkspaceRuntime, make_client, slack_error
from .store import Store
from .writes import Actor, WriteError, WriteGate

log = logging.getLogger("switchboard.slack")

APPROVE_ACTION = "switchboard_approve"
REJECT_ACTION = "switchboard_reject"

EventListener = Callable[[dict[str, Any]], Awaitable[None]]


class SlackEngine:
    """Slack workspaces for switchboard: Socket Mode or polling intake, Web API reads and writes, personas, the write
    policy and approval gate, the bot-to-bot loop guard, and optional webhook sinks. Ported from slackgate.

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
    ):
        self.env = dict(os.environ) if env is None else dict(env)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.store = Store(self.data_dir / "slack.db", clock=clock)
        self.http = http or httpx.AsyncClient()
        self.client_factory = client_factory
        self.own_post_grace = own_post_grace
        self.config = config
        self.config_error = config_error
        self.runtimes: dict[str, WorkspaceRuntime] = {}
        self.sinks: dict[str, list[WebhookSink]] = {}
        self.gate = WriteGate(self)
        self.listeners: list[EventListener] = []
        self.connect = True
        self._tasks: list[asyncio.Task] = []

    @property
    def avatar_dir(self) -> Path:
        return self.data_dir / "avatars"

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
                await self.ingest(label, payload.get("event") or {})
            elif req_type == "interactive" and payload.get("type") == "block_actions":
                await self.handle_block_actions(label, payload)
        except Exception:
            log.exception("workspace %s: failed to handle %s", label, req_type)

    async def ingest(self, label: str, event: dict[str, Any]) -> dict[str, Any] | None:
        """Turn one Slack message event into a push event: classify, dedupe, ack, fan out to sinks."""
        rt = self.runtime(label)
        channel, ts = event.get("channel"), event.get("ts")
        if not channel or not ts:
            return None
        kind = classify(event, rt.classify_context())
        if kind is None:
            return None
        if rt.user_id and event.get("user") == rt.user_id and self.own_post_grace > 0:
            await asyncio.sleep(self.own_post_grace)
        if self.store.is_sent(rt.team_id, channel, ts):
            return None
        channel_name, user_name = await asyncio.gather(rt.channel_name(channel), rt.user_name(event.get("user")))
        thread_ts = event.get("thread_ts")
        in_our_thread = bool(thread_ts and thread_ts != ts and ((rt.bot_user_id and event.get("parent_user_id") == rt.bot_user_id) or self.store.is_sent(rt.team_id, channel, thread_ts)))
        push = build_event(
            workspace=label,
            team=rt.team_id,
            event=event,
            kind=kind,
            channel_name=channel_name,
            user_name=user_name,
            team_url=rt.team_url,
            max_text_chars=self.config.max_text_chars,
            addressed_to_us=kind in ("mention", "dm") or in_our_thread,
        )
        if not self.store.add_event(push):
            return None
        if kind in ("mention", "dm") and rt.cfg.ack_reaction and "bot" in rt.clients:
            try:
                await rt.clients["bot"].reactions_add(channel=channel, timestamp=ts, name=rt.cfg.ack_reaction)
            except Exception as exc:
                log.info("ack reaction on %s failed: %s", event_id(rt.team_id, channel, ts), slack_error(exc))
        for sink in self.sinks.get(label, []):
            if sink.accepts(push):
                sink.put(push)
        for listener in self.listeners:
            try:
                await listener(push)
            except Exception:
                log.exception("workspace %s: event listener failed", label)
        return push

    async def handle_block_actions(self, label: str, payload: dict[str, Any]) -> None:
        approvals = self.config.approvals
        rt = self.runtime(label)
        user = (payload.get("user") or {}).get("id", "")
        team = (payload.get("team") or {}).get("id") or (payload.get("user") or {}).get("team_id")
        for action in payload.get("actions") or []:
            if action.get("action_id") not in (APPROVE_ACTION, REJECT_ACTION):
                continue
            rid = action.get("value", "")
            if label != approvals.notify_workspace or team != rt.team_id:
                self.store.audit(f"slack:{user}", "decision_refused", label, request_id=rid, reason="wrong workspace")
                continue
            try:
                await self.gate.decide(rid, action["action_id"] == APPROVE_ACTION, Actor("slack", user))
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

    async def _approval_target(self) -> tuple[WorkspaceRuntime, str] | None:
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

    async def notify_request(self, req: dict[str, Any]) -> None:
        target = await self._approval_target()
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
        resp = await bot.chat_postMessage(channel=dm_channel, text=f"Write request {req['id']} needs your approval", blocks=blocks)
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
