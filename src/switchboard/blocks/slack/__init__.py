"""The Slack block: the owner's Slack workspaces as a way in (events, @swb <slug> routing) and out (reads, posts,
reactions, approvals), on one shared Slack engine. Its settings are the old slack: section, plus refresh_minutes and
workspace_projects and channel_projects for the intake."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from .. import BlockError, Manifest, TargetKind
from ...slack.config import AVATAR_RE, parse_config
from ...slack.engine import SlackEngine
from .actions import SlackActions
from .credentials import TOKEN_PREFIXES, check_slack_auth, slack_workspace, validate_token
from .intake import SlackIntake

if TYPE_CHECKING:
    from ...auth import Principal
    from ...core import Switchboard

INSTRUCTIONS = """Use slack_* tools to read and write in the Slack workspaces you can access.
The bot posts with a persona. Posts as the owner wait for their approval.
Messages that mention @swb reach your inbox when addressed to you."""


class SlackBlock(SlackIntake, SlackActions):
    connect_kinds = ("slack",)
    claim_auth_check = staticmethod(check_slack_auth)
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
        self.app_icon = config.get("app_icon")
        if self.app_icon is not None and (not isinstance(self.app_icon, str) or not AVATAR_RE.fullmatch(self.app_icon)):
            raise BlockError(f"blocks.{config.get('_name', 'slack')}.app_icon must be a file name like swb.png")
        self.token_names: dict[str, list[str]] = {}
        self.token_kinds: dict[str, dict[str, str]] = {}
        if config.get("engine", True):
            parsed = parse_config(config, core.cfg.env, secret_lookup=core.secret)
            env = dict(core.cfg.env)
            for label, ws in parsed.workspaces.items():
                names = [ident.token_env for ident in ws.identities.values()]
                if ws.intake == "socket" and ws.app_token_env:
                    names.append(ws.app_token_env)
                self.token_kinds[label] = {kind: ident.token_env for kind, ident in ws.identities.items() if kind in ("bot", "user")}
                if ws.intake == "socket" and ws.app_token_env:
                    self.token_kinds[label]["app"] = ws.app_token_env
                self.token_names[label] = names
                for name in names:
                    if value := core.secret(name):
                        env[name] = value
            self.engine = SlackEngine(parsed, core.data_dir, env=env, **core.slack_options)
        super().__init__(core, config)

    def bind(self) -> None:
        for kind in TOKEN_PREFIXES:
            self.core.claims.register_kind(kind, partial(validate_token, kind), check_online=partial(self._check_claim_auth, kind))

    def send_unavailable(self) -> str:
        return "Post with slack_post. Request approval with slack_request_write. Check requests with slack_write_requests."

    async def _check_claim_auth(self, kind: str, token: str) -> str | None:
        if await self.claim_auth_check(kind, token) is False:
            return "Use a token accepted by Slack"
        return None

    def missing_token(self) -> tuple[str, str] | None:
        return next(((label, name) for label, names in self.token_names.items() for name in names if not self.core.secret(name)), None)

    def config_step(self) -> str | None:
        if self.engine is None or not self.token_names:
            return "Configure a Slack workspace and its token names in config/switchboard.yaml."
        if missing := self.missing_token():
            label, name = missing
            return f"Save the Slack token for {name} in workspace {label}."
        return None

    def next_step(self) -> str | None:
        if step := self.config_step():
            return step
        if any(not self.engine.env.get(name) for names in self.token_names.values() for name in names):
            return "Restart SwB to apply the saved Slack tokens."
        for label, ws in self.engine.config.workspaces.items():
            rt = self.engine.runtimes.get(label)
            if ws.intake == "socket" and (rt is None or not rt.connected):
                return f"Check Socket Mode for Slack workspace {label}."
        return None

    async def connect_steps(self, kind: str, principal: "Principal", project: str | None, choose: dict[str, Any] | None) -> dict[str, Any]:
        done = [f"Tokens {'are present' if all(self.core.secret(name) for name in names) else 'are ready to save'} for Slack workspace {label}."
                for label, names in self.token_names.items()]
        if missing := self.missing_token():
            label, name = missing
            group = slack_workspace(label, self.token_kinds[label])
            fields = group[2]
            if len(fields) > 1:
                names = [f"{field.label} ({field.name})" for field in fields]
                instruction = f"Save {', '.join(names[:-1])} and {names[-1]} for Slack workspace {label}."
                return {"done": done, "next": [self.group_step(principal, group, instruction)]}
            field, = fields
            return {"done": done, "next": [self.token_step(principal, name, self.next_step(), kind=field.kind, label=field.label)]}
        step = self.next_step()
        return {"done": done, "next": [{"by": "person", "do": step}] if step else []}

    def problems(self) -> list[str]:
        return SlackIntake.problems(self) + SlackActions.problems(self) if self.engine is not None else SlackIntake.problems(self)

    def status(self) -> dict[str, Any]:
        status = super().status()
        status["modes"] = self.core.modes.status(block=self.name)
        if self.app_icon is not None:
            status["manual_steps"] = [f"Upload {self.app_icon} as the app icon at api.slack.com/apps, Basic Information, Display Information"]
        return status

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
