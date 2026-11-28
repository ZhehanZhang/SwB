from functools import partial

import pytest
import yaml
from starlette.testclient import TestClient

from slackfix import ENV, FakeWorld, base_config
from switchboard.app import create_app
from switchboard.bot_identity import BotIdentity
from switchboard.core import Switchboard
from switchboard.slack.config import Grant

PUBLIC = "https://bot-icons.example.test"
MESSAGE = {"channel": "CWATCH001", "text": "Hello"}


@pytest.fixture
def setup_slack(tmp_path):
    def setup(*, scopes="chat:write, chat:write.customize", public_base_url=PUBLIC):
        settings = {**base_config(), "public_base_url": public_base_url}
        for workspace in settings["workspaces"].values():
            workspace.pop("sinks", None)
        path = tmp_path / "switchboard.yaml"
        path.write_text(yaml.safe_dump({"version": 2, "blocks": {"slack": {"use": "slack", **settings}}}))
        world = FakeWorld()
        world.bot_scopes = scopes
        core = Switchboard(path, tmp_path / "data", env=ENV, slack_options={"client_factory": world.factory, "connect": False})
        return core, world

    return setup


def test_slack_bot_identity_inherits_each_field_and_preserves_agent_personas(setup_slack):
    core, world = setup_slack()
    with TestClient(create_app(core)) as client:
        slack = core.blocks["slack"]
        assert slack.bot_identity_capabilities() == (True, None)
        assert slack.bot_identity_capabilities("a2a") == (True, None)
        assert slack.bot_identity_capabilities("private") == (False, None)
        assert slack.bot_identity_capabilities("unknown") == (False, None)
        rt = core.slack.runtimes["a2a"]
        default = Grant(id="agent", persona=None, allow_persona_override=False, allowed_personas=None, scopes={"a2a": ("bot",)})
        default_post = partial(core.slack.gate.submit, default, "a2a", "bot", "post_message", MESSAGE)
        client.portal.call(default_post)
        global_icon = client.portal.call(core.bot_identities.save_icon, b"global icon")
        client.portal.call(core.bot_identities.set, "slack", "", BotIdentity("Release", global_icon))
        client.portal.call(default_post)
        client.portal.call(core.bot_identities.set, "slack", "a2a", BotIdentity("Build Room", ""))
        client.portal.call(default_post)
        workspace_icon = client.portal.call(core.bot_identities.save_icon, b"workspace icon")
        client.portal.call(core.bot_identities.set, "slack", "a2a", BotIdentity("", workspace_icon))
        client.portal.call(default_post)
        client.portal.call(core.slack.gate.submit, core.slack.config.grant("full"), "a2a", "bot", "post_message", MESSAGE)
        client.portal.call(rt.execute, "user", "post_message", MESSAGE, None)
        client.portal.call(core.bot_identities.set, "slack", "a2a", None)
        client.portal.call(default_post)
        posts = [(token, args) for token, method, args in world.calls if method == "chat_postMessage"]
        assert posts == [
            ("xoxb-a", {**MESSAGE, "username": "Gate", "icon_emoji": ":robot_face:"}),
            ("xoxb-a", {**MESSAGE, "username": "Release", "icon_url": f"{PUBLIC}/avatars/{global_icon}"}),
            ("xoxb-a", {**MESSAGE, "username": "Build Room", "icon_url": f"{PUBLIC}/avatars/{global_icon}"}),
            ("xoxb-a", {**MESSAGE, "username": "Release", "icon_url": f"{PUBLIC}/avatars/{workspace_icon}"}),
            ("xoxb-a", {**MESSAGE, "username": "Capy", "icon_url": "https://img.example/capy.png"}),
            ("xoxp-a", MESSAGE),
            ("xoxb-a", {**MESSAGE, "username": "Release", "icon_url": f"{PUBLIC}/avatars/{global_icon}"}),
        ]


def test_slack_without_customization_scope_sends_with_native_bot_identity(setup_slack):
    core, world = setup_slack(scopes="chat:write, chat:write.customize.invalid")
    with TestClient(create_app(core)) as client:
        slack = core.blocks["slack"]
        assert slack.bot_identity_capabilities("a2a") == (False, "https://api.slack.com/apps")
        assert slack.bot_identity_capabilities() == slack.bot_identity_capabilities("a2a")
        client.portal.call(core.bot_identities.set, "slack", "a2a", BotIdentity("Ignored", "swb.png"))
        rt = core.slack.runtimes["a2a"]
        client.portal.call(rt.execute, "bot", "post_message", MESSAGE, {"name": "Ignored too", "icon_emoji": ":ghost:"})
        assert [args for _, method, args in world.calls if method == "chat_postMessage"] == [MESSAGE]


def test_slack_returns_the_apps_oauth_permissions_page_when_scope_is_missing(setup_slack):
    core, world = setup_slack(scopes="chat:write, users:read")
    world.canned["bots_info"] = lambda *_: {"bot": {"app_id": "A012345678"}}
    with TestClient(create_app(core)):
        block = core.blocks["slack"]
        assert block.bot_identity_capabilities("a2a") == (False, "https://api.slack.com/apps/A012345678/oauth")


def test_slack_name_is_customizable_without_public_icon_host(setup_slack):
    core, world = setup_slack(public_base_url=None)
    with TestClient(create_app(core)) as client:
        slack = core.blocks["slack"]
        assert slack.bot_identity_capabilities("a2a") == (True, None)
        client.portal.call(core.bot_identities.set, "slack", "", BotIdentity("Release", "swb.png"))
        client.portal.call(core.slack.runtimes["a2a"].execute, "bot", "post_message", MESSAGE, None)
        assert [args for _, method, args in world.calls if method == "chat_postMessage"] == [
            {**MESSAGE, "username": "Release", "icon_emoji": ":electric_plug:"}
        ]
