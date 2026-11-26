"""The Slack block: the owner's Slack workspaces as a way in (events, @swb <slug> routing) and out (reads, posts,
reactions, approvals), on one shared Slack engine. Its settings are the old slack: section, plus refresh_minutes and
workspace_projects and channel_projects for the intake."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import Manifest, TargetKind
from ...slack.config import parse_config
from ...slack.engine import SlackEngine
from .actions import SlackActions
from .intake import SlackIntake

if TYPE_CHECKING:
    from ...core import Switchboard

INSTRUCTIONS = """Slack: slack_* tools read and write the owner's Slack workspaces under your grant; posts by the bot use a
persona, posts as the owner wait for his approval. Messages that mention @swb reach your inbox when they are for you."""


class SlackBlock(SlackIntake, SlackActions):
    manifest = Manifest(
        id="slack",
        title="Slack",
        kind="both",
        level_out="L3",
        level_in="events",
        verbs={"reply": "reply"},
        tools=("workspaces", "channels", "search", "history", "thread", "watched", "events", "personas", "targets", "post", "react",
               "request_write", "write_request", "write_requests", "wait"),
        targets=(TargetKind("channel"),),
        personas="config",
        secrets=("SLACK_<WORKSPACE>_BOT_TOKEN", "SLACK_<WORKSPACE>_APP_TOKEN", "SLACK_<WORKSPACE>_USER_TOKEN"),
        network=("slack.com",),
        identities=({"id": "bot", "policy": "free|approval|never"}, {"id": "user", "policy": "approval|never"}),
        instructions=INSTRUCTIONS,
    )

    def __init__(self, core: "Switchboard", config: dict[str, Any]):
        if config.get("engine", True):
            self.engine = SlackEngine(parse_config(config, core.cfg.env), core.data_dir, env=core.cfg.env, **core.slack_options)
        super().__init__(core, config)

    def problems(self) -> list[str]:
        return SlackIntake.problems(self) + SlackActions.problems(self) if self.engine is not None else SlackIntake.problems(self)

    async def start(self) -> None:
        if self.engine is not None:
            await self.engine.start(connect=self.core.slack_connect)

    async def aclose(self) -> None:
        if self.engine is not None:
            await self.engine.stop()

    def mcp_tools(self, mcp: Any, kit: Any) -> None:
        from .mcp_tools import register

        register(mcp, self.core, kit.who, kit.wait_caps)


SlackOrigin = SlackDestination = SlackBlock
