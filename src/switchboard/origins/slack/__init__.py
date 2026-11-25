"""The Slack origin: events from the owner's Slack workspaces (watched channels, mentions, DMs) arrive here.

The shared Slack engine runs the intake (Socket Mode per workspace, or polling), dedupes events, drops our own posts and
marks what is addressed to us. This origin files each event in the inbox of every agent with a Slack grant on that
workspace, where capy_wait, capy_inbox and slack_wait return it. Webhook sinks in slack.workspaces.<label>.sinks still
receive events as before.

origins.slack.workspaces.<label>.project names the switchboard project a workspace's events belong to (a project
filter on the inbox then finds them).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import Origin

if TYPE_CHECKING:
    from ...core import Switchboard


class SlackOrigin(Origin):
    name = "slack"

    def __init__(self, core: "Switchboard", config: dict[str, Any]):
        super().__init__(core, config)
        self.projects = {str(k): str((v or {}).get("project") or "") or None for k, v in (config.get("workspaces") or {}).items()}
        if core.slack is not None:
            core.slack.listeners.append(self.deliver)

    def problems(self) -> list[str]:
        return [] if self.core.slack is not None else ["origins.slack is configured but the config has no slack section"]

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
        for agent, grant in sorted(engine.config.grants.items()):
            if grant.identities_for(push["workspace"]):
                await self.core.inbox.add(self._handle(agent, push["workspace"]), "slack_event",
                                          {"event_id": push["id"], "slack_kind": push["kind"], "addressed_to_us": push["addressed_to_us"],
                                           "text": push["text"], "truncated": push["truncated"], "files": push["files"], "from": sender})
