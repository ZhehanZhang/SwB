from unittest.mock import AsyncMock

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
    assert "state" not in result and 'connect(what="chatgpt")' in out
    core.auth._issue("fixture-client", core.cfg.resource, "chatgpt")
    result, out = check()
    assert result["state"] == "connected" and result["next"] == []
    assert "connected" in out
    core.store.x("UPDATE oauth_tokens SET revoked_at = ? WHERE source = 'chatgpt'", (core.clock(),))
    result, out = check()
    assert "state" not in result and 'connect(what="chatgpt")' in out


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
