from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from .config import Grant
from .personas import resolve_persona
from .slack import slack_error

if TYPE_CHECKING:
    from .engine import SlackEngine as Core

log = logging.getLogger("switchboard.slack.writes")

DECIDER_KINDS = ("slack", "admin")
OWNER_RELAY = "relay:owner"
"""The one key that posts as the owner without approval: only the dots block holds it, only for owner-trust senders (his
own Slack messages and his own agents), and only into a dot DM or group DM of his, or the dots relay channel. Every other user-identity post keeps its write policy."""


class WriteError(RuntimeError):
    pass


@dataclass(frozen=True)
class Actor:
    """Who decides a write request. Agents (MCP callers) never get an Actor that can decide."""

    kind: str
    id: str
    workspace: str | None = None

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}"


class WriteGate:
    def __init__(self, core: "Core"):
        self.core = core

    async def submit(
        self,
        key: Grant,
        workspace: str,
        identity: str,
        action: str,
        params: dict[str, Any],
        persona_id: str | None = None,
        force_approval: bool = False,
        *,
        thread_only: bool = False,
    ) -> dict[str, Any]:
        cfg = self.core.config
        store = self.core.store
        ws = cfg.workspaces.get(workspace)
        if ws is None or identity not in key.identities_for(workspace):
            raise WriteError(f"agent {key.id!r} may not use {identity} in {workspace!r}")
        policy = ws.identities[identity].write
        if key.id == OWNER_RELAY:
            if not (identity == "user" and action == "post_message" and (workspace, params.get("channel")) in self.core.owner_channels):
                raise WriteError("the owner relay key posts only as the owner, into a relay channel")
            policy = "free"
        if policy == "never":
            store.audit(f"agent:{key.id}", "write_refused", workspace, identity, action=action, params=params, reason="policy never")
            raise WriteError(f"writes as {identity} in {workspace} are disabled (policy: never)")
        thread_persona = (self.core.thread_persona(workspace, params.get("channel"), params.get("thread_ts"), agent=key.id)
                          if action == "post_message" and not thread_only else None)
        persona = resolve_persona(cfg, ws, identity, key, persona_id, self.core.personas(), thread_persona) if action == "post_message" else None
        persona_d = asdict(persona) if persona else None
        loop_guard = False
        if identity == "bot" and action == "post_message" and params.get("thread_ts") and policy == "free" and not force_approval:
            count = store.bot_posts_in_thread(workspace, params["channel"], params["thread_ts"], since=self.core.clock() - 3600)
            if count >= ws.bot_thread_posts_per_hour:
                loop_guard = True
                store.audit(f"agent:{key.id}", "loop_guard", workspace, identity, channel=params["channel"], thread_ts=params["thread_ts"], posts_last_hour=count, limit=ws.bot_thread_posts_per_hour)
        if policy == "approval" or force_approval or loop_guard:
            if thread_only:
                raise WriteError("thread-only reply would need approval")
            req = store.create_request(
                workspace=workspace,
                identity=identity,
                action=action,
                params=params,
                persona=persona_d,
                key_id=key.id,
                ttl_seconds=cfg.approvals.ttl_minutes * 60,
            )
            store.audit(f"agent:{key.id}", "write_requested", workspace, identity, request_id=req["id"], action=action, params=params, persona=persona_d)
            try:
                await self.core.notify_request(req)
            except Exception as exc:
                log.warning("approval notice for %s failed: %s", req["id"], slack_error(exc))
            result = {
                "status": "pending_approval",
                "request_id": req["id"],
                "expires_at": req["expires_at"],
                "note": "Queued for the owner's approval out of band (Slack DM or the switchboard page). Agents cannot approve; check slack_write_requests later.",
            }
            if loop_guard:
                result["reason"] = f"loop guard: {ws.bot_thread_posts_per_hour} bot posts in this thread in the last hour"
            return result
        return {"status": "sent", **await self._execute(workspace, identity, action, params, persona_d, actor=f"agent:{key.id}")}

    async def _execute(self, workspace: str, identity: str, action: str, params: dict, persona: dict | None, actor: str, request_id: str | None = None) -> dict[str, Any]:
        store = self.core.store
        try:
            result = await self.core.runtime(workspace).execute(identity, action, params, persona)
        except Exception as exc:
            store.audit(actor, "write_failed", workspace, identity, action=action, params=params, request_id=request_id, error=slack_error(exc))
            raise WriteError(f"{action} failed: {slack_error(exc)}") from exc
        store.audit(actor, "write_sent", workspace, identity, action=action, params=params, persona=persona, request_id=request_id, result=result)
        return result

    async def decide(self, request_id: str, approve: bool, actor: Actor) -> dict[str, Any]:
        store = self.core.store
        cfg = self.core.config
        if actor.kind not in DECIDER_KINDS:
            store.audit(str(actor), "decision_refused", request_id=request_id, reason="not an owner channel")
            raise WriteError("write requests are decided only by the owner, out of band")
        approvers = cfg.approvers()
        if actor.kind == "slack" and not ((actor.workspace, actor.id) in approvers if actor.workspace else actor.id in {u for _, u in approvers}):
            store.audit(str(actor), "decision_refused", request_id=request_id, reason="not the approver")
            raise WriteError("only the configured approver can decide write requests")
        req = store.get_request(request_id)
        if req is None:
            raise WriteError(f"no write request {request_id}")
        if req["status"] == "pending" and self.core.clock() >= req["expires_at"]:
            await self.sweep()
            req = store.get_request(request_id)
        if req["status"] != "pending":  # type: ignore[index]
            raise WriteError(f"request {request_id} is already {req['status']}")  # type: ignore[index]
        now = self.core.clock()
        if not approve:
            if not store.transition_request(request_id, "pending", "rejected", decided_at=now, decided_by=str(actor)):
                raise WriteError(f"request {request_id} changed state; reload")
            store.audit(str(actor), "write_rejected", req["workspace"], req["identity"], request_id=request_id)
        else:
            if not store.transition_request(request_id, "pending", "approved", decided_at=now, decided_by=str(actor)):
                raise WriteError(f"request {request_id} changed state; reload")
            store.audit(str(actor), "write_approved", req["workspace"], req["identity"], request_id=request_id)
            try:
                result = await self._execute(req["workspace"], req["identity"], req["action"], req["params"], req["persona"], actor=str(actor), request_id=request_id)
                store.transition_request(request_id, "approved", "sent", result=result)
            except WriteError as exc:
                store.transition_request(request_id, "approved", "failed", result={"error": str(exc)})
        final = store.get_request(request_id)
        await self._close_notice(final)  # type: ignore[arg-type]
        return final  # type: ignore[return-value]

    async def sweep(self) -> list[dict[str, Any]]:
        expired = self.core.store.expire_requests()
        for req in expired:
            self.core.store.audit("system", "write_expired", req["workspace"], req["identity"], request_id=req["id"])
            await self._close_notice(req)
        return expired

    async def _close_notice(self, req: dict[str, Any]) -> None:
        try:
            await self.core.close_notice(req)
        except Exception as exc:
            log.warning("could not update approval notice for %s: %s", req["id"], slack_error(exc))
