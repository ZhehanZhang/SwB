from unittest.mock import AsyncMock

import httpx
import pytest
import yaml

from conftest import PUBLIC, STATIC_KEY, base_config, call, payload
from switchboard import __main__, cli


def test_dots_connect_requires_a_live_mailbox_credential(make, tmp_path, monkeypatch, capsys):
    config = base_config(version=2, blocks={"mcp": {}, "dots": {
        "delivery": "mailbox", "dots": {"example-dot": {"mailbox": "chatgpt"}},
    }})
    core, client = make(config, env={cli.OWNER_KEY: STATIC_KEY})
    (tmp_path / ".env").write_text(f"{cli.OWNER_KEY}={STATIC_KEY}\n")

    def check():
        result = payload(call(client, STATIC_KEY, "connect", {"what": "dots"}))
        monkeypatch.setattr(cli, "_call", AsyncMock(return_value=result))
        cli.main(["connect", "dots", "--dir", str(tmp_path), "--url", PUBLIC])
        return result, capsys.readouterr().out

    result, out = check()
    assert "state" not in result and "sign in to SwB" in out
    core.auth._issue("fixture-client", core.cfg.resource, "chatgpt")
    result, out = check()
    assert result["state"] == "connected" and result["next"] == []
    assert "connected" in out
    core.store.x("UPDATE oauth_tokens SET revoked_at = ? WHERE source = 'chatgpt'", (core.clock(),))
    result, out = check()
    assert "state" not in result and "sign in to SwB" in out


def test_dots_connect_accepts_a_static_mailbox_key(make):
    config = base_config(version=2, blocks={"mcp": {}, "dots": {
        "delivery": "mailbox", "dots": {"example-dot": {"mailbox": "dot-helper"}},
    }})
    core, client = make(config, env={cli.OWNER_KEY: STATIC_KEY, "SWITCHBOARD_KEY_DOT_HELPER": "example-dot-key-value-only"})
    assert core.auth.verify_bearer("example-dot-key-value-only").source == "dot-helper"
    result = payload(call(client, STATIC_KEY, "connect", {"what": "dots"}))
    assert result["state"] == "connected" and result["next"] == []


def test_connect_reports_missing_provider_credentials_only_in_next(make):
    cfg = base_config(version=2, blocks={"mcp": {}, "github": {}, "discord": {"connect": False}, "telegram": {"connect": False}})
    _, client = make(cfg, env={cli.OWNER_KEY: STATIC_KEY})
    for provider in ("github", "discord", "telegram"):
        result = payload(call(client, STATIC_KEY, "connect", {"what": provider}))
        assert result["next"][0]["by"] == "person" and result["next"][0]["link"]
        assert not any(word in step.lower() for word in ("missing", "not connected", "none") for step in result["done"])


@pytest.mark.parametrize("has_owner", [False, True])
def test_connect_completion_distinguishes_required_and_optional_steps(make, monkeypatch, has_owner):
    owners = [222222] if has_owner else []
    cfg = base_config(version=2, blocks={"mcp": {}, "telegram": {
        "connect": False, "spaces": {"home": {"chats": [-1001111111111], "owner_ids": owners}},
    }})
    core, client = make(cfg, env={cli.OWNER_KEY: STATIC_KEY, "TELEGRAM_BOT_TOKEN": "fixture-bot-token"})
    monkeypatch.setattr(core.blocks["telegram"], "connected", lambda: True)

    result = payload(call(client, STATIC_KEY, "connect", {"what": "telegram"}))

    assert result["done"]
    assert [step.get("optional", False) for step in result["next"]] == ([True] if has_owner else [False, True])
    assert result["complete"] is has_owner
    assert (result.get("state") == "connected") is has_owner


def test_github_setup_reports_mode_and_project_targets_separately_from_sinks(make):
    cfg = base_config(version=2, blocks={"mcp": {}, "github": {"spaces": {
        "acme/configured": {"mode": "watch"},
        "acme/routed": {"mode": "assist", "project": "demo"},
    }}})
    core, client = make(cfg, env={cli.OWNER_KEY: STATIC_KEY})
    core.projects.create(slug="demo", name="Demo")
    assert payload(call(client, STATIC_KEY, "routes_set", {"project": "demo", "target": "agent:claude"}))["project"] == "demo"
    args = {"block": "github", "space": "acme/configured", "mode": "assist", "target": "agent:claude"}
    assert payload(call(client, STATIC_KEY, "space_mode", args))["target"] == "agent:claude"
    connect = payload(call(client, STATIC_KEY, "connect", {"what": "github"}))
    status = payload(call(client, STATIC_KEY, "github_status"))
    for report in (connect, status):
        assert (report["repository_spaces"], report["bound_repositories"], report["sink_routes"]) == (2, 2, 0)
    assert "GitHub repositories with a target 2." in connect["done"]


def test_signed_in_status_explains_github_installation_access_failure(make, monkeypatch):
    cfg = base_config(version=2, blocks={"mcp": {}, "github": {"spaces": {"acme/app": {"mode": "watch"}}}})
    core, client = make(cfg, env={cli.OWNER_KEY: STATIC_KEY, "GITHUB_WEBHOOK_SECRET": "fixture-webhook-secret"})
    api = core.blocks["github"].api
    monkeypatch.setattr(api, "auth_type", lambda: "app")
    monkeypatch.setattr(api, "app_jwt", lambda: "fixture-app-jwt")
    denied = AsyncMock(return_value=httpx.Response(401))
    monkeypatch.setattr(api.http, "request", denied)
    assert client.get("/healthz").status_code == 200
    denied.assert_not_called()

    step = "Check the GitHub App credentials and repository access."
    result = payload(call(client, STATIC_KEY, "switchboard_status"))
    health = client.get("/healthz", headers={"Authorization": f"Bearer {STATIC_KEY}"}).json()
    denied.assert_awaited_once()
    for status in (result, health):
        assert status["status"] == "degraded" and step in status["next_steps"]
        assert status["blocks"]["github"]["next_step"] == step
    assert step in cli._display("switchboard_status", result)


def test_blocks_check_uses_status_missing_credentials(make, tmp_path, monkeypatch, capsys):
    cfg = base_config(version=2, blocks={
        "mcp": {}, "github": {}, "slack": {"workspaces": {"sample": {
            "team_id": "T0DEMO0001", "intake": "socket", "app_token_env": "FIXTURE_SLACK_APP_TOKEN",
            "identities": {"bot": {"token_env": "FIXTURE_SLACK_BOT_TOKEN", "write": "free"}},
        }}},
    })
    core, client = make(cfg, env={cli.OWNER_KEY: STATIC_KEY})
    setup = payload(call(client, STATIC_KEY, "switchboard_status", {}))["block_setup"]
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(cfg))
    monkeypatch.setenv("SWITCHBOARD_CONFIG", str(config))
    monkeypatch.setenv("SWITCHBOARD_DATA_DIR", str(tmp_path / "check-data"))
    __main__.cli_main(["blocks", "check"])
    out = capsys.readouterr().out
    for block in ("slack", "github"):
        assert setup[block]["missing_env"]
        for key in setup[block]["missing_env"]:
            assert f"{block} requires {key}." in out
        assert f"{block} is ready." not in out


def test_blocks_check_separates_required_config_from_live_setup(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"version": 2, "blocks": {
        "mcp": {}, "dots": {"delivery": "mailbox"}, "slack-dots": {"use": "dots", "workspace": "sample"},
        "github": {"secret_env": "FIXTURE_GITHUB_SECRET"},
        "slack": {"workspaces": {"sample": {
            "team_id": "T0DEMO0001", "intake": "socket", "app_token_env": "FIXTURE_SLACK_APP_TOKEN",
            "identities": {"bot": {"token_env": "FIXTURE_SLACK_BOT_TOKEN"}},
        }}},
    }}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SWITCHBOARD_CONFIG", str(config))
    monkeypatch.setenv("SWITCHBOARD_DATA_DIR", str(tmp_path / "check-data"))
    monkeypatch.setenv("FIXTURE_SLACK_APP_TOKEN", "example-app-token")
    monkeypatch.setenv("FIXTURE_SLACK_BOT_TOKEN", "example-bot-token")
    monkeypatch.setenv("FIXTURE_GITHUB_SECRET", "example-webhook-secret-value")

    __main__.cli_main(["blocks", "check"])

    assert set(capsys.readouterr().out.splitlines()) == {
        "mcp is ready.",
        "webhook is ready.",
        "slack is ready.",
        "slack-dots is ready.",
        "dots needs setup. Declare a dot under dots in the dots block config.",
        "github needs setup. Add a GitHub repository route or space in config/switchboard.yaml and restart SwB.",
    }
