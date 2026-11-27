from __future__ import annotations

import json
import re
from dataclasses import replace

import httpx
import pytest
import yaml

from conftest import CAPY_KEY, OWNER, PUBLIC, STATIC_KEY, base_config, call, default_env, payload, rpc, sign_in
from switchboard.auth import Principal
from switchboard.blocks.capy.api import CapyAPI
from switchboard.core import Switchboard


def connect(client, what, **kw):
    result = call(client, STATIC_KEY, "connect", {"what": what, **kw})
    assert not result.get("isError"), result
    return payload(result)


def test_connect_schema_and_unknown_kinds(make):
    _, client = make(config=base_config(version=2, blocks={"mcp": {}}))
    tools = rpc(client, STATIC_KEY, "tools/list").json()["result"]["tools"]
    tool = next(t for t in tools if t["name"] == "connect")
    assert tool["title"] == "Connect to SwB"
    assert "call connect first" in tool["description"]
    assert "until complete is true" in tool["description"]
    assert set(tool["inputSchema"]["properties"]) == {"what", "project", "choose"}
    out = connect(client, "unknown")
    assert out["what"] == "unknown"
    assert out["complete"] is False
    assert out["kinds"] == ["capy-captain", "chatgpt", "claude", "claude-project", "cloudflare", "coding-agent", "discord",
                            "dots", "github", "grok", "grok-bot", "mcp-client", "muse", "slack", "telegram"]


@pytest.mark.parametrize(("kind", "block", "env_names"), [
    ("capy-captain", "capy", ("CAPY_API_KEY", "CAPY_API_KEY_SERVICE")),
    ("claude-project", "claude-routine", ("CLAUDE_ROUTINE_ID", "CLAUDE_ROUTINE_TOKEN")),
    ("discord", "discord", ("DISCORD_BOT_TOKEN",)),
    ("dots", "dots", ()),
    ("github", "github", ("GITHUB_WEBHOOK_SECRET",)),
    ("grok-bot", "grok-routine", ("GROK_ROUTINE_URL", "GROK_ROUTINE_SECRET")),
    ("slack", "slack", ("SLACK_A2A_APP_TOKEN", "SLACK_A2A_BOT_TOKEN")),
    ("telegram", "telegram", ("TELEGRAM_BOT_TOKEN",)),
])
def test_connect_offers_working_config_for_disabled_block(make, kind, block, env_names):
    cfg = base_config(version=2, blocks={"mcp": {}, block: {"enabled": False}})
    env = {"SWITCHBOARD_KEY_OWNER": STATIC_KEY}
    _, client = make(config=cfg, env=env)
    out = connect(client, kind)
    assert out["what"] == kind and out["done"] == []
    assert out["complete"] is False
    configure, restart = out["next"]
    assert configure["by"] == "you" and "config/switchboard.yaml" in configure["do"]
    assert "link" not in configure
    assert all(name in configure["do"] for name in env_names)
    assert (".env" in configure["do"]) == bool(env_names)
    assert restart == {"by": "you", "do": "Restart SwB."}
    snippet = configure["snippet"]
    assert snippet.startswith(f"  {block}:\n")
    parsed = yaml.safe_load(snippet)
    assert set(parsed) == {block}
    assert parsed[block]["use"] == block and parsed[block]["enabled"] is True
    cfg["blocks"].update(parsed)
    core, enabled = make(config=cfg, env=env)
    assert block in core.blocks
    if kind == "dots":
        assert core.blocks[block].dots()[0]["mailbox"] == "chatgpt"
        progress = connect(enabled, kind)
        assert "state" not in progress
        [step] = progress["next"]
        assert step["by"] == "person" and "https://chatgpt.com/plugins" in step["do"]
        assert "sign in to SwB" in step["do"] and "connect(" not in step["do"]
    elif kind == "discord":
        assert core.blocks[block].guild_spaces == {"100000000000000001": "home"}
        assert core.blocks[block].spaces["home"].owner_ids == {"100000000000000002"}
    elif kind == "telegram":
        assert core.blocks[block]._groups == {-1001111111111: "home"}
        assert core.blocks[block].spaces["home"].owner_ids == {"222222"}
    elif kind == "slack":
        assert set(core.blocks[block].token_names["a2a"]) == set(env_names)
        assert "link" in connect(enabled, kind)["next"][0]
        workspaces = payload(call(enabled, STATIC_KEY, "slack_workspaces"))["workspaces"]
        assert [(w["workspace"], list(w["identities"])) for w in workspaces] == [("a2a", ["bot"])]
    elif kind == "github":
        assert core.blocks[block].registered_repositories() == {"example-org/app"}
        assert parsed[block]["spaces"]["example-org/app"]["mode"] == {"mode": "watch", "target": "agent:beta"}
        assert "Replace agent:beta with an address from targets." in configure["do"]


def test_connect_uses_chosen_dot_in_the_config_snippet(make):
    _, client = make(config=base_config(version=2, blocks={"mcp": {}}))
    configure, _ = connect(client, "dots", choose={"dot": "Research Dot"})["next"]
    assert yaml.safe_load(configure["snippet"])["dots"]["dots"] == {
        "research-dot": {"name": "Research Dot", "mailbox": "chatgpt"},
    }


def test_connect_checks_the_chosen_dot_in_an_enabled_mailbox_block(make):
    cfg = base_config(version=2, blocks={"mcp": {}, "dots": {"delivery": "mailbox", "dots": {
        "first-dot": {"mailbox": "chatgpt"}, "second-dot": {"mailbox": "research-helper"},
    }}})
    core, client = make(config=cfg)
    core.auth._issue("fixture-client", core.cfg.resource, "chatgpt")

    assert connect(client, "dots", choose={"dot": "First Dot"})["state"] == "connected"
    [step] = connect(client, "dots", choose={"dot": "Second Dot"})["next"]
    assert step["by"] == "person" and "dot mailbox" in step["do"]
    add, restart = connect(client, "dots", choose={"dot": "Third Dot"})["next"]
    assert "blocks.dots.dots" in add["do"]
    assert yaml.safe_load(add["snippet"]) == {"third-dot": {"name": "Third Dot", "mailbox": "chatgpt"}}
    assert restart == {"by": "you", "do": "Restart SwB."}


def test_connect_snippet_keeps_environment_discovered_blocks_after_reload(make):
    cfg = {"version": 2, "public_url": PUBLIC}
    env = {
        "SWITCHBOARD_KEY_OWNER": STATIC_KEY,
        "GITHUB_WEBHOOK_SECRET": "fixture-github-webhook-secret",
        "GROK_ROUTINE_DEMO_URL": "https://example.test/demo-webhook",
        "GROK_ROUTINE_DEMO_SECRET": "fixture-grok-webhook-secret",
    }
    core, client = make(config=cfg, env=env)
    assert set(core.blocks) == {"mcp", "github", "grok-routine"}
    configure, restart = connect(client, "dots")["next"]
    assert configure["by"] == "you" and "Add this blocks section" in configure["do"]
    assert restart["do"] == "Restart SwB."
    snippet = configure["snippet"]
    assert all(value not in snippet for value in env.values())
    parsed = yaml.safe_load(snippet)
    assert set(parsed) == {"blocks"}
    for name, config in core.cfg.blocks.items():
        assert parsed["blocks"][name] == config
    reloaded, client = make(config={**cfg, **parsed}, env=env)
    assert set(reloaded.blocks) == {"mcp", "github", "grok-routine", "dots"}
    assert "state" not in connect(client, "dots")


@pytest.mark.parametrize(("kind", "block", "routine_env"), [
    ("claude-project", "claude-routine", {
        "CLAUDE_ROUTINE_ID": "trig_fixture",
        "CLAUDE_ROUTINE_TOKEN": "fixture-claude-routine-token",
    }),
    ("grok-bot", "grok-routine", {
        "GROK_ROUTINE_URL": "https://example.test/demo-webhook",
        "GROK_ROUTINE_SECRET": "fixture-grok-webhook-secret",
    }),
])
def test_connect_enables_routine_through_environment_discovery(make, kind, block, routine_env):
    cfg = {"version": 2, "public_url": PUBLIC}
    env = {"SWITCHBOARD_KEY_OWNER": STATIC_KEY}
    core, client = make(config=cfg, env=env)
    assert set(core.blocks) == {"mcp"}
    configure, restart = connect(client, kind)["next"]
    assert configure == {"by": "person", "do": f"Set {' and '.join(routine_env)} in .env."}
    assert restart == {"by": "you", "do": "Restart SwB."}
    reloaded, client = make(config=cfg, env={**env, **routine_env})
    assert set(reloaded.blocks) == {"mcp", block}
    [routine] = reloaded.blocks[block].routines().values()
    assert routine["source"] == "config"
    assert set(reloaded.cfg.block_env[block]) == set(routine_env)
    assert "mcp-client" in connect(client, "unknown")["kinds"]


@pytest.mark.parametrize(("kind", "menu"), [
    ("chatgpt", "https://chatgpt.com/plugins"), ("claude", "Customize > Connectors"), ("grok", "grok.com > Connectors"),
])
def test_connect_hosted_clients(make, kind, menu):
    _, client = make()
    out = connect(client, kind)
    assert out["done"] == [f"MCP URL: {PUBLIC}/mcp.", "OAuth sign-in: available."]
    [step] = out["next"]
    assert step["by"] == "person" and menu in step["do"]
    assert f"{PUBLIC}/mcp" in step["do"] and "OAuth" in step["do"]


@pytest.mark.parametrize("kind", ["claude", "chatgpt", "grok", "muse"])
def test_connect_hosted_clients_require_https_even_with_local_oauth(make, kind):
    core, client = make(config=base_config(public_url="http://localhost:8000", version=2, blocks={"mcp": {}, "grok-routine": {}}))
    assert core.cfg.owner_sign_in
    out = connect(client, kind)
    [step] = out["next"]
    assert step["by"] == "person"
    assert "swb up --tunnel cloudflare" in step["do"]
    assert "https://swb.one/docs/first-run#get-a-public-address" in step["do"]
    assert "owner sign-in" not in step["do"]
    assert step == connect(client, "grok-bot")["next"][0]
    assert "localhost" not in json.dumps(out)
    assert "http://localhost:8000/mcp" in connect(client, "mcp-client")["done"][0]


def test_connect_hosted_resume_after_oauth_sign_in(make):
    _, client = make()
    tokens = sign_in(client)
    out = connect(client, "claude")
    assert out["state"] == "connected" and out["next"] == []
    assert tokens["access_token"] not in json.dumps(out) and tokens["refresh_token"] not in json.dumps(out)


def test_connect_pairing_resume(make):
    core, client = make()
    [choose] = connect(client, "mcp-client")["next"]
    assert choose["by"] == "you" and choose["call"]["tool"] == "connect"
    choice = {"agent": "local-script"}
    out = connect(client, "mcp-client", choose=choice)
    [step] = out["next"]
    assert step["by"] == "person" and f"POST {PUBLIC}/connect" in step["do"]
    code = re.search(r'"code":"([A-Z0-9-]+)"', step["do"])[1]
    assert "Bearer <key>" in step["do"] and STATIC_KEY not in json.dumps(out)
    assert core.audit.recent(action="connect_mcp-client")
    assert not any(code in json.dumps(row) for row in core.audit.recent())
    key = client.post("/connect", json={"code": code}).json()["key"]
    assert key not in json.dumps(out)
    assert client.post("/connect", json={"code": code}).status_code == 400
    assert connect(client, "mcp-client", choose=choice)["state"] == "connected"


@pytest.mark.parametrize("restriction", [
    {"scopes": ()}, {"scopes": ("*",)}, {"tools": ("connect",)}, {"targets": ("*",)}, {"kind": "connect"}, {"trust": "known"},
])
def test_connect_read_only_for_every_limited_principal(make, monkeypatch, restriction):
    cfg = base_config(version=2, blocks={"mcp": {}, "capy": {}, "claude-routine": {}, "github": {}})
    core, client = make(config=cfg)
    principal = replace(Principal("owner", "key:owner", "key"), **restriction)
    monkeypatch.setattr(core.auth, "verify_bearer", lambda _token: principal)
    requests = {
        "mcp-client": {"agent": "local-script"},
        "capy-captain": {"captain": "Example Captain"},
        "claude-project": {"fire_url": "trig_123456789", "claude_project": "Example", "project": "example"},
        "github": {},
        "telegram": {},
    }
    for kind, choose in requests.items():
        out = connect(client, kind, choose=choose)
        assert out["next"] and all(step["by"] == "person" and "link" not in step and "call" not in step for step in out["next"])
        assert CAPY_KEY not in json.dumps(out)
    assert core.routes.list() == []
    assert not core.store.q("SELECT 1 FROM connect_codes")
    assert not core.store.q("SELECT 1 FROM routine_claims")
    assert not core.store.q("SELECT 1 FROM secrets")


def test_block_status_and_token_setup(make, monkeypatch):
    cfg = base_config(version=2, blocks={
        "mcp": {},
        "capy": {},
        "claude-routine": {},
        "slack": {"workspaces": {"example": {"team_id": "T0123ABCD", "identities": {"bot": {"token_env": "SLACK_EXAMPLE_BOT_TOKEN"}}}}},
        "github": {},
        "dots": {"workspace": "other"},
    })
    env = {"SWITCHBOARD_KEY_CAPY": STATIC_KEY}
    core, client = make(config=cfg, env=env)

    async def offline(kind, token):
        assert kind == "slack_bot" and token.startswith("xoxb-")
        return None

    monkeypatch.setattr(core.blocks["slack"], "claim_auth_check", offline)
    status = core.status()
    assert status["blocks"]["mcp"]["next_step"] is None
    for name, hint in {"capy": "key", "claude-routine": "routine", "slack": "SLACK_EXAMPLE_BOT_TOKEN",
                       "github": "webhook secret", "dots": "Slack workspace"}.items():
        step = status["blocks"][name]["next_step"]
        assert hint in step and step in status["next_steps"]
    for kind in ("slack", "github"):
        [step] = connect(client, kind)["next"]
        assert step["by"] == "person" and step["link"].startswith(f"{PUBLIC}/claim/")
        assert step["do"] == status["blocks"][kind]["next_step"]
        token = ("xoxb-" if kind == "slack" else "") + "test-token-kept-out-of-output"
        page = client.get(step["link"], headers=OWNER)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
        saved = client.post(step["link"], headers=OWNER, data={"csrf": csrf, "token": token})
        assert saved.status_code == 200 and token not in saved.text
        out = connect(client, kind)
        assert "Restart SwB" in out["next"][0]["do"]
        assert "present" in out["done"][0] and token not in json.dumps(out)
    assert "Slack workspace" in connect(client, "dots")["next"][0]["do"]
    assert "dots" in connect(client, "unrecognized")["kinds"]


def test_slack_connect_names_every_credential_on_the_grouped_form(make):
    _, client = make(base_config(version=2, blocks={"mcp": {}, "slack": {"workspaces": {
        "example": {"team_id": "T0123ABCD", "intake": "socket", "app_token_env": "EXAMPLE_APP",
                    "identities": {"bot": {"token_env": "EXAMPLE_BOT"}, "user": {"token_env": "EXAMPLE_USER"}}},
    }}}))

    step, = connect(client, "slack")["next"]
    assert step["by"] == "person" and "Slack workspace example" in step["do"]
    page = client.get(step["link"], headers=OWNER)
    assert page.status_code == 200
    for label, name in (("Bot token", "EXAMPLE_BOT"), ("User token", "EXAMPLE_USER"), ("App-level token", "EXAMPLE_APP")):
        assert f"{label} ({name})" in step["do"]
        assert f">{label}</label>" in page.text and f'name="{name}"' in page.text


def test_slack_app_icon_is_a_manual_step_in_status(make):
    _, client = make(config=base_config(version=2, blocks={"mcp": {}, "slack": {"app_icon": "swb.png"}}))
    status = payload(call(client, STATIC_KEY, "switchboard_status"))
    assert status["destinations"]["slack"]["manual_steps"] == [
        "Upload swb.png as the app icon at api.slack.com/apps, Basic Information, Display Information",
    ]


def test_status_missing_oauth_and_missing_github_routes(make):
    cfg = {"version": 2, "blocks": {"mcp": {}, "github": {}}}
    secret = "local-webhook-test-value"
    core, client = make(config=cfg, env={**default_env(), "GITHUB_WEBHOOK_SECRET": secret})
    status = payload(call(client, STATIC_KEY, "switchboard_status"))
    assert status["blocks"]["mcp"]["next_step"] is None
    assert status["blocks"]["mcp"]["manual_steps"] == ["Give SwB a public HTTPS address for hosted MCP clients."]
    assert status["blocks"]["github"]["next_step"].startswith("Add a GitHub repository route")
    assert status["next_steps"] == core.status()["next_steps"] and secret not in json.dumps(status)
    out = connect(client, "claude")
    assert out["done"] == []
    [step] = out["next"]
    assert "swb up --tunnel cloudflare" in step["do"]
    assert "Set up owner sign-in for OAuth." in step["do"]


async def test_saved_slack_and_github_tokens_loaded_on_restart(tmp_path):
    config = tmp_path / "switchboard.yaml"
    config.write_text(yaml.safe_dump({"version": 2, "blocks": {
        "slack": {"workspaces": {"example": {
            "team_id": "T0123ABCD", "app_token_env": "EXAMPLE_APP_TOKEN",
            "identities": {"bot": {"token_env": "EXAMPLE_BOT_TOKEN"}},
        }}},
        "github": {"routes": {"example/repo": ["listener"]}, "sinks": {"listener": {}}},
    }}))
    data = tmp_path / "data"
    core = Switchboard(config, data, env={})
    tokens = {"EXAMPLE_APP_TOKEN": "mock-app-token", "EXAMPLE_BOT_TOKEN": "mock-bot-token", "GITHUB_WEBHOOK_SECRET": "mock-hook-secret"}
    try:
        for name, token in tokens.items():
            url = core.claims.mint(name, PUBLIC)
            core.claims.consume(url.rsplit("/", 1)[-1], token)
        assert "Restart SwB" in core.status()["blocks"]["slack"]["next_step"]
        assert "Restart SwB" in core.status()["blocks"]["github"]["next_step"]
    finally:
        await core.aclose()

    core = Switchboard(config, data, env={})
    try:
        assert core.slack.env["EXAMPLE_APP_TOKEN"] == tokens["EXAMPLE_APP_TOKEN"]
        assert core.slack.env["EXAMPLE_BOT_TOKEN"] == tokens["EXAMPLE_BOT_TOKEN"]
        status = core.status()
        assert status["blocks"]["slack"]["next_step"].startswith("Check Socket Mode")
        assert status["blocks"]["github"]["secret_present"] is True
        assert status["blocks"]["github"]["next_step"] is None
        assert all(token not in json.dumps(status) for token in tokens.values())
    finally:
        await core.aclose()


async def test_capy_captain_discovery_reads_all_pages():
    cursors = []

    def response(request):
        cursor = request.url.params.get("cursor")
        cursors.append(cursor)
        if cursor is None:
            return httpx.Response(200, json={"items": [{"id": "jam_first"}], "cursor": "1:next"})
        assert cursor == "1:next"
        return httpx.Response(200, json={"items": [{"id": "jam_second"}], "cursor": None})

    api = CapyAPI("https://capy.example.test/v1", CAPY_KEY, transport=httpx.MockTransport(response))
    try:
        assert await api.list_captains() == [{"id": "jam_first"}, {"id": "jam_second"}]
        assert cursors == [None, "1:next"]
    finally:
        await api.aclose()
