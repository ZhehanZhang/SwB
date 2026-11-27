from __future__ import annotations

import asyncio
import base64
import json
import os
from unittest.mock import patch

import httpx
import pytest
import yaml
from starlette.testclient import TestClient

from switchboard import __main__, cli
from switchboard.app import create_app
from switchboard.blocks import BlockError
from switchboard.blocks.mcp.events import EVENT
from switchboard.chat import Incoming
from switchboard.cli import _display
from switchboard.config import Branding, ConfigError, load_config
from switchboard.core import Switchboard

from conftest import SECOND_KEY, STATIC_KEY, acall, arpc, eventually, principal


def test_no_config_starts_only_mcp_and_reports_missing_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    core = Switchboard(env={})
    with TestClient(create_app(core)) as client:
        health = client.get("/healthz").json()
    assert set(health["blocks"]) == {"mcp"}
    assert health["block_setup"]["mcp"] == {"enabled": True, "missing_env": []}
    assert health["block_setup"]["capy"] == {"enabled": False, "missing_env": ["CAPY_API_KEY", "CAPY_API_KEY_SERVICE"]}
    assert health["block_setup"]["slack"]["missing_env"] == ["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"]
    assert health["block_setup"]["discord"] == {"enabled": False, "missing_env": ["DISCORD_BOT_TOKEN"]}
    assert health["block_setup"]["telegram"] == {"enabled": False, "missing_env": ["TELEGRAM_BOT_TOKEN"]}
    assert health["block_setup"]["dots"] == {"enabled": False, "missing_env": []}
    assert core.cfg.branding == Branding("SwB", "swb", None, "#64748b", None)
    assert core.cfg.timezone == "UTC"
    assert (tmp_path / "data" / "switchboard.db").is_file()
    assert "capy: off (missing env: CAPY_API_KEY, CAPY_API_KEY_SERVICE)" in _display("switchboard_status", health)


@pytest.mark.parametrize(("block", "token", "missing"), [
    ("capy", "CAPY_API_KEY", ["CAPY_API_KEY_SERVICE"]),
    ("capy", "CAPY_API_KEY_SERVICE", ["CAPY_API_KEY"]),
    ("capy", "CAPY_API_KEY_AS_EXAMPLE", ["CAPY_API_KEY", "CAPY_API_KEY_SERVICE"]),
    ("github", "GITHUB_WEBHOOK_SECRET", []),
    ("discord", "DISCORD_BOT_TOKEN", []),
    ("telegram", "TELEGRAM_BOT_TOKEN", []),
    ("slack", "SLACK_BOT_TOKEN", ["SLACK_TEAM_ID"]),
    ("slack", "SLACK_APP_TOKEN", ["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"]),
    ("slack", "SLACK_ACME_USER_TOKEN", ["SLACK_ACME_TEAM_ID"]),
    ("claude-routine", "CLAUDE_ROUTINE_TOKEN", ["CLAUDE_ROUTINE_ID"]),
    ("claude-routine", "CLAUDE_ROUTINE_TOKEN_ACME", ["CLAUDE_ROUTINE_ID_ACME"]),
    ("grok-routine", "GROK_ROUTINE_SECRET", ["GROK_ROUTINE_URL"]),
    ("grok-routine", "GROK_ROUTINE_ACME_SECRET", ["GROK_ROUTINE_ACME_URL"]),
])
def test_token_env_loads_its_block_and_status_never_includes_credential_values(tmp_path, monkeypatch, block, token, missing):
    monkeypatch.chdir(tmp_path)
    marker = "synthetic-test-credential-value"
    core = Switchboard(env={token: marker})
    try:
        status = core.status()
        assert set(status["blocks"]) == {"mcp", block}
        assert status["block_setup"][block] == {"enabled": True, "missing_env": missing}
        assert marker not in str(status)
        assert marker not in repr(core.cfg)
    finally:
        asyncio.run(core.aclose())


async def test_env_chat_blocks_do_not_allow_unconfigured_intake_or_posts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = {"DISCORD_BOT_TOKEN": "fixture-discord", "TELEGRAM_BOT_TOKEN": "fixture-telegram"}
    core = Switchboard(env=env)
    try:
        assert set(core.blocks) == {"mcp", "discord", "telegram"}
        for name, token in (("discord", "DISCORD_BOT_TOKEN"), ("telegram", "TELEGRAM_BOT_TOKEN")):
            block = core.blocks[name]
            assert block.token() == env[token]
            assert block.dm_space is None
            assert await block.ingest(Incoming("default", "123", "456", "789", "hello", dm=True)) is None
            with pytest.raises(BlockError, match="has no grant"):
                await core.invoke(principal("owner"), name, block.action("post"), {"space": "default", "channel": "123", "text": "hello"})
    finally:
        await core.aclose()


def test_yaml_chat_token_overrides_and_explicit_blocks_take_precedence(tmp_path):
    base = tmp_path / "base.yaml"
    base.write_text("version: 2\nblocks:\n  community: {use: discord, token_env: CUSTOM_DISCORD_TOKEN}\n  telegram: {enabled: false}\n")
    cfg = load_config(base, {"DISCORD_BOT_TOKEN": "fixture-discord", "TELEGRAM_BOT_TOKEN": "fixture-telegram"})
    assert cfg.blocks == {"community": {"use": "discord", "token_env": "CUSTOM_DISCORD_TOKEN"}}
    assert cfg.block_env["community"] == ("CUSTOM_DISCORD_TOKEN",)


async def test_config_with_retired_capy_burst_still_loads(tmp_path):
    config = tmp_path / "switchboard.yaml"
    config.write_text("version: 2\nblocks:\n  capy: {burst: {limit: 5}, captain_delivery: steer}\n")
    core = Switchboard(config, data_dir=tmp_path, env={})
    try:
        assert core.blocks["capy"].delivery == "steer"
    finally:
        await core.aclose()


async def test_mcp_events_deliver_mailbox_messages_without_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    core = Switchboard(env={"SWITCHBOARD_KEY_OWNER": STATIC_KEY, "SWITCHBOARD_KEY_SECOND": SECOND_KEY})
    assert set(core.blocks) == {"mcp"}
    delivered = []

    async def resolve(host, port):
        return ["8.8.8.8"]

    def receive(request):
        body = json.loads(request.content)
        if body.get("type") == "verification":
            return httpx.Response(200, json={"challenge": body["challenge"]})
        delivered.append(body)
        return httpx.Response(204)

    core.blocks["mcp"].events.resolver = resolve
    core.blocks["mcp"].events.transport = httpx.MockTransport(receive)
    app = create_app(core)
    async with app.starlette.router.lifespan_context(app.starlette):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            response = await arpc(client, SECOND_KEY, "server/discover", modern=True)
            assert response.json()["result"]["capabilities"]["events"] == {}
            response = await arpc(client, SECOND_KEY, "events/list", modern=True)
            assert [event["name"] for event in response.json()["result"]["events"]] == [EVENT]
            subscription = {"name": EVENT, "arguments": {"mailbox_id": "second"}, "delivery": {
                "mode": "webhook", "url": "https://receiver.example.test/hook",
                "secret": "whsec_" + base64.b64encode(b"a" * 32).decode(),
            }}
            response = await arpc(client, SECOND_KEY, "events/subscribe", subscription, modern=True)
            assert response.json()["result"]["id"].startswith("sub_")
            await acall(client, STATIC_KEY, "send", {"to": "agent:second", "task": "env-only mailbox message"}, modern=True)
            await eventually(lambda: len(delivered) == 1)
            [event] = delivered
            assert event["name"] == EVENT and event["data"]["mailbox_id"] == "second"
            message = await acall(client, SECOND_KEY, "get_message", {"message_id": event["data"]["message_id"]}, modern=True)
            assert message["structuredContent"]["text"] == "env-only mailbox message"


def test_complete_env_builds_workspaces_and_routines_and_sets_server_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(env={
        "PUBLIC_URL": "https://switchboard.example.test/",
        "SWITCHBOARD_DATA_DIR": str(tmp_path / "instance"),
        "SWITCHBOARD_OWNER_EMAILS": " Owner@Example.Test, second@example.test ",
        "CLOUDFLARE_ACCESS_TEAM_DOMAIN": "acme.cloudflareaccess.com",
        "CLOUDFLARE_ACCESS_AUD": "example-audience",
        "SLACK_ACME_BOT_TOKEN": "fixture-bot", "SLACK_ACME_APP_TOKEN": "fixture-app", "SLACK_ACME_TEAM_ID": "T0EXAMPLE1",
        "CLAUDE_ROUTINE_TOKEN_ACME": "fixture-routine", "CLAUDE_ROUTINE_ID_ACME": "trig_EXAMPLE",
        "GROK_ROUTINE_SECRET": "fixture-grok", "GROK_ROUTINE_URL": "https://hooks.example.test/routine",
    })
    assert cfg.public_url == "https://switchboard.example.test"
    assert cfg.data_dir == tmp_path / "instance"
    assert cfg.owner_emails == ("owner@example.test", "second@example.test")
    assert cfg.owner_sign_in
    assert cfg.blocks["slack"]["workspaces"]["acme"] == {
        "team_id": "T0EXAMPLE1", "identities": {"bot": {"token_env": "SLACK_ACME_BOT_TOKEN"}},
        "app_token_env": "SLACK_ACME_APP_TOKEN",
    }
    assert cfg.blocks["slack"]["agents"] == {"owner": {"workspaces": {"acme": ["bot"]}}}
    assert cfg.blocks["claude-routine"]["routines"]["acme"]["routine_id"] == "trig_EXAMPLE"
    assert cfg.blocks["grok-routine"]["routines"]["grok"]["url_env"] == "GROK_ROUTINE_URL"


def test_overlay_deep_merges_mappings_and_replaces_lists_over_yaml_and_env(tmp_path):
    base, overlay = tmp_path / "base.yaml", tmp_path / "private.yaml"
    base.write_text(yaml.safe_dump({
        "version": 2, "auth": {"owner_emails": ["first@example.test", "unused@example.test"],
                               "cloudflare_access": {"team_domain": "acme.cloudflareaccess.com", "aud": "example-aud"}},
        "public_url": "https://base.example.test", "data_dir": "/srv/switchboard/data",
        "branding": {"display_name": "Acme", "accent_color": "#445566", "logo_assets_dir": "/srv/switchboard/logos"},
        "blocks": {"mcp": {"use": "mcp"}, "capy": {"identity": "personal"}, "github": {"secret_env": "EXAMPLE_WEBHOOK_SECRET"}},
    }))
    overlay.write_text(yaml.safe_dump({
        "auth": {"owner_emails": ["private@example.test"]},
        "public_url": "https://private.example.test/",
        "branding": {"icon": "/logos/acme.svg", "logo_assets_dir": None},
        "blocks": {"capy": {"enabled": False}},
    }))
    cfg = load_config(env={
        "SWITCHBOARD_CONFIG": str(base), "SWITCHBOARD_OVERLAY": str(overlay),
        "SWITCHBOARD_PUBLIC_URL": "https://env.example.test", "PUBLIC_URL": "https://alt.example.test",
        "SWITCHBOARD_OWNER_EMAILS": "env@example.test", "SWITCHBOARD_DATA_DIR": "/env-data",
        "CAPY_API_KEY": "fixture-capy", "GITHUB_WEBHOOK_SECRET": "unused-default-secret", "SLACK_BOT_TOKEN": "fixture-bot",
    })
    assert cfg.public_url == "https://private.example.test"
    assert cfg.data_dir.as_posix() == "/srv/switchboard/data"
    assert cfg.owner_emails == ("private@example.test",)
    assert cfg.access_team_domain == "acme.cloudflareaccess.com" and cfg.access_aud == "example-aud"
    assert cfg.branding == Branding("Acme", "swb", "/logos/acme.svg", "#445566", None)
    assert set(cfg.blocks) == {"mcp", "github"}
    assert cfg.block_env["github"] == ("EXAMPLE_WEBHOOK_SECRET",)


def test_local_overlay_is_optional_and_explicit_overlay_path_wins(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config"
    config.mkdir()
    (config / "local.yaml").write_text("branding: {display_name: Local}\n")
    assert load_config(env={}).branding.display_name == "Local"
    explicit = tmp_path / "overlay.yaml"
    with pytest.raises(ConfigError, match="overlay file not found"):
        load_config(env={"SWITCHBOARD_OVERLAY": str(explicit)})
    explicit.write_text("branding: {display_name: Private}\n")
    assert load_config(env={"SWITCHBOARD_OVERLAY": str(explicit)}).branding.display_name == "Private"


def test_v2_without_blocks_uses_env_and_route_overlay_keeps_v1_blocks(tmp_path):
    base, overlay = tmp_path / "base.yaml", tmp_path / "overlay.yaml"
    base.write_text("version: 2\n")
    assert set(load_config(base, {"GITHUB_WEBHOOK_SECRET": "fixture-github"}).blocks) == {"mcp", "github"}
    base.write_text("destinations: {capy: {daily_cap: 3}}\n")
    overlay.write_text("version: 2\nblocks: {github: {use: github}}\n")
    assert load_config(base, {"SWITCHBOARD_OVERLAY": str(overlay)}).blocks == {
        "mcp": {"use": "mcp"}, "capy": {"use": "capy", "daily_cap": 3}, "github": {"use": "github"},
    }


def test_env_aliases_and_yaml_null_overrides(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = {"SWITCHBOARD_PUBLIC_URL": "https://primary.example.test", "PUBLIC_URL": "https://secondary.example.test",
           "SWITCHBOARD_DATA": "legacy-data", "SWITCHBOARD_OWNER_EMAILS": "owner@example.test",
           "CLOUDFLARE_ACCESS_TEAM_DOMAIN": "acme.cloudflareaccess.com", "CLOUDFLARE_ACCESS_AUD": "example-aud"}
    cfg = load_config(env=env)
    assert cfg.public_url == env["SWITCHBOARD_PUBLIC_URL"] and cfg.data_dir.as_posix() == "legacy-data" and cfg.owner_sign_in
    base = tmp_path / "base.yaml"
    base.write_text("version: 2\npublic_url: null\nauth: {cloudflare_access: null, owner_emails: []}\nblocks: {}\n")
    explicit = load_config(base, env)
    assert explicit.public_url is None and not explicit.owner_emails and not explicit.owner_sign_in and not explicit.blocks


def test_swb_up_without_dir_preserves_process_and_dotenv_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SWITCHBOARD_CONFIG=from-env.yaml\nSWITCHBOARD_DATA_DIR=dotenv-data\n")
    called = []
    monkeypatch.setattr(__main__, "main", lambda args, **_: called.append((args, load_config(), os.environ["SWITCHBOARD_CONFIG"])))
    with patch.dict(os.environ, {"SWITCHBOARD_DATA_DIR": "process-data"}, clear=True):
        cli.main(["up"])
        assert "SWITCHBOARD_CONFIG" not in os.environ
    args, cfg, config_path = called[0]
    assert config_path == "from-env.yaml"
    assert args == ["serve"] and cfg.data_dir.as_posix() == "process-data" and set(cfg.blocks) == {"mcp"}
