"""Resolve the agent that receives a message addressed to the owner."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .auth import Principal
    from .core import Switchboard

OWNER_ACTIVE_WINDOW_S = 1800
OWNER_DELIVERY_PROBLEM = "owner_delivery_pending"


class OwnerDelivery:
    def __init__(self, core: Switchboard):
        self.core = core

    def record_activity(self, principal: Principal) -> None:
        if principal.kind not in ("key", "oauth") or principal.trust != "owner" or not principal.credential or principal.source == "owner":
            return
        now = self.core.clock()

        def write(db):
            db.execute("DELETE FROM owner_activity WHERE at < ?", (now - OWNER_ACTIVE_WINDOW_S,))
            db.execute("INSERT OR REPLACE INTO owner_activity(agent, kind, client, credential, at) VALUES (?, ?, ?, ?, ?)",
                       (principal.source, principal.kind, principal.client_id, principal.credential, now))

        self.core.store.tx(write)

    def resolve(self, project: str | None, block: str | None = None, space: str | None = None) -> tuple[str, str] | None:
        scope = f"project:{project}" if project else "general"

        def can_read(agent: str) -> bool:
            scopes = self.core.cfg.key_scopes.get(agent)
            tools = self.core.cfg.key_tools.get(agent)
            return (bool(agent) and agent != "owner" and (scopes is None or "*" in scopes or scope in scopes)
                    and (tools is None or "inbox" in tools or "wait" in tools))

        store = self.core.store
        for row in store.q("""SELECT agent, kind, client, credential FROM owner_activity WHERE at >= ?
                               ORDER BY at DESC, rowid DESC""", (self.core.clock() - OWNER_ACTIVE_WINDOW_S,)):
            agent = row["agent"]
            if can_read(agent) and self.core.auth.grant_active(
                source=agent, kind=row["kind"], client_id=row["client"], credential=row["credential"],
            ):
                return agent, "active"

        if project:
            route = store.one("SELECT target FROM routes WHERE destination = 'agent' AND agent = '' AND project = ?", (project,))
            if route and (agent := json.loads(route["target"]).get("agent")) and can_read(agent):
                return agent, "project"
            mode = self.core.modes.cfg.projects.get(project)
            section = self.core.modes._section(block, space) if block and space else {}
            for target in (mode.target if mode else None, section.get("default_target")):
                if target:
                    agent = self.core.modes._mailbox(str(target).strip().lower(), project)
                    if can_read(agent):
                        return agent, "project"

        route = store.one("SELECT target FROM routes WHERE destination = 'agent' AND agent = 'owner' AND project = ''")
        if route and (agent := json.loads(route["target"]).get("agent")) and can_read(agent):
            return agent, "global"
        return None

    def audit(self, handle: str, source: str, kind: str, data: dict[str, Any], recipient: str | None, resolved_by: str | None) -> None:
        sender = data.get("from") or {}
        self.core.audit.record(origin=sender.get("origin") or ("system" if kind == "watch_digest" else "mcp"),
                               source=source or data.get("block", "system"), client=sender.get("client_id") or "",
                               destination="mailbox", action="owner_delivery", outcome="ok" if recipient else "pending",
                               target=recipient, ref=handle, detail=resolved_by or OWNER_DELIVERY_PROBLEM)
