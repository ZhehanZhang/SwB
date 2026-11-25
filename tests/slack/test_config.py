import copy

import pytest
import yaml

from slackfix import ENV, ROOT, base_config
from switchboard.slack.config import ConfigError, parse_config
from slackfix import clock, hooks, make_core, world  # noqa: F401  (fixtures)


def test_example_config_parses():
    data = yaml.safe_load((ROOT / "config" / "switchboard.example.yaml").read_text())["slack"]
    cfg = parse_config(data, {})
    assert set(cfg.workspaces) == {"a2a", "private"}
    assert cfg.workspaces["private"].user_only
    assert cfg.workspaces["private"].ack_reaction is None
    assert cfg.workspaces["a2a"].identities["user"].write == "approval"
    assert cfg.personas["helper"].icon_url == "https://switchboard.example.com/avatars/helper.png"
    assert cfg.grant("capy").identities_for("a2a") == ("bot",) and cfg.grant("other-agent") is None


def test_api_keys_and_access_now_come_from_switchboard():
    with pytest.raises(ConfigError, match="api_keys became agents"):
        parse_config({**base_config(), "api_keys": []}, ENV)
    with pytest.raises(ConfigError, match="auth.cloudflare_access"):
        parse_config({**base_config(), "access": {}}, ENV)


def test_defaults_and_sinks():
    cfg = parse_config(base_config(), ENV)
    a = cfg.workspaces["a2a"]
    assert a.intake == "socket" and a.has_bot and a.ack_reaction == "eyes"
    sink = a.sinks[0]
    assert sink.url == ENV["SINK_A"] and sink.headers == {"Authorization": ENV["SINK_AUTH"]}
    assert sink.kinds == ("watched", "mention", "dm") and sink.coalesce_seconds == 0


def mutate(fn):
    data = copy.deepcopy(base_config())
    fn(data)
    return data


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d["workspaces"]["private"]["identities"].update({"bot": {"token_env": "X"}}), "must not have a bot identity"),
        (lambda d: d["workspaces"]["private"]["identities"]["user"].update({"write": "free"}), "approval' or 'never'"),
        (lambda d: d["workspaces"]["private"].update({"default_persona": "gate"}), "personas do not apply"),
        (lambda d: d["workspaces"]["a2a"].update({"team_id": "nope"}), "team_id"),
        (lambda d: d["workspaces"]["a2a"]["identities"]["bot"].update({"write": "sometimes"}), "write must be one of"),
        (lambda d: d["agents"]["full"].update({"persona": "ghost"}), "not a defined persona"),
        (lambda d: d["agents"]["fixed"]["workspaces"].update({"private": ["bot"]}), "has no 'bot' identity"),
        (lambda d: d["agents"]["fixed"]["workspaces"].update({"nowhere": ["bot"]}), "unknown workspace"),
        (lambda d: d["personas"].update({"x": {"name": "X", "icon_url": "http://insecure"}}), "https"),
        (lambda d: d["personas"].update({"x": {"name": "X", "avatar": "x.png"}}), "public_base_url"),
        (lambda d: d["approvals"].update({"notify_workspace": "private"}), "bot identity"),
        (lambda d: d["workspaces"]["a2a"].pop("app_token_env"), None),
    ],
)
def test_validation(change, message):
    data = mutate(change)
    if message is None:
        assert parse_config(data, ENV).workspaces["a2a"].intake == "poll"
        return
    with pytest.raises(ConfigError, match=message):
        parse_config(data, ENV)
