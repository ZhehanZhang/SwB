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


def test_connect_schema_unknown_and_disabled_blocks(make):
    _, client = make(config=base_config(version=2, blocks={"mcp": {}}))
    tools = rpc(client, STATIC_KEY, "tools/list").json()["result"]["tools"]
    tool = next(t for t in tools if t["name"] == "connect")
    assert tool["description"].startswith('When someone asks "how do I connect/set up/link X to switchboard?", call connect')
    assert set(tool["inputSchema"]["properties"]) == {"what", "project", "choose"}
    out = connect(client, "unknown")
    assert out["kinds"] == ["capy-captain", "chatgpt", "claude", "claude-project", "coding-agent", "discord", "github",
                            "grok", "grok-bot", "mcp-client", "muse", "slack", "telegram"]
    for kind in ("capy-captain", "claude-project", "slack", "github", "grok-bot", "muse"):
        [step] = connect(client, kind)["next"]
        assert step["by"] == "person" and "Enable" in step["do"] and "link" not in step


@pytest.mark.parametrize(("kind", "menu"), [("chatgpt", "https://chatgpt.com/plugins"), ("claude", "Customize > Connectors")])
def test_connect_hosted_clients(make, kind, menu):
    _, client = make()
    out = connect(client, kind)
    assert out["done"] == [f"MCP URL: {PUBLIC}/mcp.", "OAuth sign-in: available."]
    [step] = out["next"]
    assert step["by"] == "person" and menu in step["do"]
    assert "OAuth" in step["do"]


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
    }
    for kind, choose in requests.items():
        out = connect(client, kind, choose=choose)
        assert out["next"] and all(step["by"] == "person" and "link" not in step and "call" not in step for step in out["next"])
        assert CAPY_KEY not in json.dumps(out)
    assert core.routes.list() == []
    assert not core.store.q("SELECT 1 FROM connect_codes")
    assert not core.store.q("SELECT 1 FROM routine_claims")
    assert not core.store.q("SELECT 1 FROM secrets")


def test_block_status_and_token_setup(make):
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
        token = "test-token-kept-out-of-output"
        page = client.get(step["link"], headers=OWNER)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
        saved = client.post(step["link"], headers=OWNER, data={"csrf": csrf, "token": token})
        assert saved.status_code == 200 and token not in saved.text
        out = connect(client, kind)
        assert "Restart Switchboard" in out["next"][0]["do"]
        assert "present" in out["done"][0] and token not in json.dumps(out)
    assert "Slack workspace" in connect(client, "dots")["next"][0]["do"]
    assert "dots" in connect(client, "unrecognized")["kinds"]


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
    assert status["blocks"]["mcp"]["next_step"].startswith("Set public_url")
    assert status["blocks"]["github"]["next_step"].startswith("Add a GitHub repository route")
    assert status["next_steps"] == core.status()["next_steps"] and secret not in json.dumps(status)
    out = connect(client, "claude")
    assert out["done"] == ["MCP URL: http://localhost:8000/mcp.", "OAuth sign-in: unavailable."]
    assert "owner sign-in" in out["next"][0]["do"]


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
        assert "Restart Switchboard" in core.status()["blocks"]["slack"]["next_step"]
        assert "Restart Switchboard" in core.status()["blocks"]["github"]["next_step"]
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
