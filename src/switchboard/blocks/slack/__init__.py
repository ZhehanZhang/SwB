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
    from ...auth import Principal
    from ...core import Switchboard

INSTRUCTIONS = """Slack: slack_* tools read and write the owner's Slack workspaces under your grant; posts by the bot use a
persona, posts as the owner wait for his approval. Messages that mention @swb reach your inbox when they are for you."""


class SlackBlock(SlackIntake, SlackActions):
    connect_kinds = ("slack",)
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
        self.token_names: dict[str, list[str]] = {}
        if config.get("engine", True):
            parsed = parse_config(config, core.cfg.env)
            env = dict(core.cfg.env)
            for label, ws in parsed.workspaces.items():
                names = [ident.token_env for ident in ws.identities.values()]
                if ws.intake == "socket" and ws.app_token_env:
                    names.append(ws.app_token_env)
                self.token_names[label] = names
                for name in names:
                    if value := core.secret(name):
                        env[name] = value
            self.engine = SlackEngine(parsed, core.data_dir, env=env, **core.slack_options)
        super().__init__(core, config)

    def missing_token(self) -> tuple[str, str] | None:
        return next(((label, name) for label, names in self.token_names.items() for name in names if not self.core.secret(name)), None)

    def next_step(self) -> str | None:
        if self.engine is None or not self.token_names:
            return "Configure a Slack workspace and its token names in config/switchboard.yaml."
        if missing := self.missing_token():
            label, name = missing
            return f"Save the Slack token for {name} in workspace {label}."
        if any(not self.engine.env.get(name) for names in self.token_names.values() for name in names):
            return "Restart Switchboard to apply the saved Slack tokens."
        for label, ws in self.engine.config.workspaces.items():
            rt = self.engine.runtimes.get(label)
            if ws.intake == "socket" and (rt is None or not rt.connected):
                return f"Check Socket Mode for Slack workspace {label}."
        return None

    async def connect_steps(self, kind: str, principal: "Principal", project: str | None, choose: dict[str, Any] | None) -> dict[str, Any]:
        done = [f"Slack workspace {label}: tokens {'present' if all(self.core.secret(name) for name in names) else 'missing'}."
                for label, names in self.token_names.items()]
        step = self.next_step()
        if missing := self.missing_token():
            return {"done": done, "next": [self.token_step(principal, missing[1], step)]}
        return {"done": done, "next": [{"by": "person", "do": step}] if step else []}

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
