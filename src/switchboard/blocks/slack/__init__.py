"""The Slack block: the owner's Slack workspaces as a way in (events, @swb <slug> routing) and out (reads, posts,
reactions, approvals), on one shared Slack engine. Its settings are the old slack: section, plus refresh_minutes and
workspace_projects and channel_projects for the intake."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from .. import Manifest, TargetKind
from ...bot_identity import DEFAULT_ICON, DEFAULT_NAME
from ...slack.config import parse_config
from ...slack.engine import SlackEngine
from ...slack.slack import BotPostIdentity
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
            self.engine = SlackEngine(parsed, core.data_dir, env=env, bot_identity=self._bot_identity, **core.slack_options)
        super().__init__(core, config)

    def _bot_identity(self, space: str) -> BotPostIdentity:
        identities = self.core.bot_identities
        local, default = identities.get(self.name, space), identities.get(self.name)
        name = (local.name if local else None) or (default.name if default else None)
        icon = (local.icon if local else None) or (default.icon if default else None)
        args: BotPostIdentity = {}
        if local and local.post_every_agent:
            args["post_every_agent"] = True
            name = name or DEFAULT_NAME
            icon = icon or DEFAULT_ICON
        if name:
            args["username"] = name
        if icon and (base := self.identity_public_url()):
            args["icon_url"] = f"{base}/avatars/{icon}"
        return args

    def identity_public_url(self) -> str | None:
        if self.engine is not None and self.engine.config.public_base_url:
            return self.engine.config.public_base_url
        url = self.core.cfg.public_url
        return url.rstrip("/") if url and url.startswith("https://") else None

    def bot_identity_capabilities(self, space: str | None = None) -> tuple[bool, str | None]:
        runtimes = self.engine.runtimes if self.engine is not None else {}
        if space is not None and space not in runtimes:
            return False, None
        selected = (runtimes[space],) if space is not None else runtimes.values()
        bots = [rt for rt in selected if "bot" in rt.clients]
        if not bots:
            return False, None
        if not any(rt.bot_can_customize for rt in bots):
            apps = {rt.bot_app_id for rt in bots}
            app = next(iter(apps)) if len(apps) == 1 else None
            return False, f"https://api.slack.com/apps/{app}/oauth" if app else "https://api.slack.com/apps"
        return True, None

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
        status.update(self.core.modes.chat_drop_status(self.name))
        return status

    def status_for(self, principal: Principal) -> dict[str, Any]:
        if principal.is_unrestricted_owner:
            return self.status()
        grant = self.engine.config.grant(principal.source) if self.engine is not None else None
        return {"workspaces": {} if grant is None else {
            label: {"intake": runtime.cfg.intake} for label, runtime in self.engine.runtimes.items() if grant.identities_for(label)
        }, "modes": self.core.modes.status(principal, self.name)}

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
