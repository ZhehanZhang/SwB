"""Workspace B (user_only): no bot anywhere, never join or invite."""

import re

import pytest
import yaml

from slackfix import ROOT
from switchboard.slack.slack import FORBIDDEN_METHODS, ForbiddenSlackMethod, make_client
from slackfix import clock, hooks, make_core, world  # noqa: F401  (fixtures)

BOT_SCOPES_OR_EVENTS = ("bot_user", "bot_events", "app_mentions", "bot")


def test_b_manifest_has_no_bot():
    manifest = yaml.safe_load((ROOT / "manifests" / "workspace-b-user-only.yaml").read_text())
    assert "bot_user" not in (manifest.get("features") or {})
    assert set(manifest["oauth_config"]["scopes"]) == {"user"}
    events = manifest["settings"]["event_subscriptions"]
    assert set(events) == {"user_events"}
    flat = yaml.safe_dump(manifest)
    assert "app_mention" not in flat and "bot_user" not in flat and "bot_events" not in flat
    assert manifest["settings"]["interactivity"]["is_enabled"] is False
    for scope in manifest["oauth_config"]["scopes"]["user"]:
        assert scope not in ("channels:join", "channels:write.invites", "groups:write.invites", "channels:manage", "groups:write")


def test_a_manifest_has_personas_scope():
    manifest = yaml.safe_load((ROOT / "manifests" / "workspace-a-agent.yaml").read_text())
    assert "chat:write.customize" in manifest["oauth_config"]["scopes"]["bot"]
    assert "channels:join" not in manifest["oauth_config"]["scopes"]["bot"]


@pytest.mark.parametrize("method", ["conversations_join", "conversations_invite", "conversations_inviteShared"])
async def test_client_refuses_join_and_invite(method):
    client = make_client("xoxp-not-a-real-token")
    with pytest.raises(ForbiddenSlackMethod):
        await getattr(client, method)(channel="C0123456", users="U1", emails=["a@b.c"])
    with pytest.raises(ForbiddenSlackMethod):
        await client.api_call("conversations.join", params={"channel": "C0123456"})


def test_no_source_calls_join_or_invite():
    src = ROOT / "src" / "switchboard"
    pattern = re.compile(r"(conversations|channels|groups)[._](join|invite)", re.I)
    offenders = []
    files = list(src.rglob("*.py"))
    assert any(p.parent.name == "slack" for p in files)
    for path in files:
        for no, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line) and "FORBIDDEN" not in line and not line.strip().startswith('"'):
                offenders.append(f"{path.name}:{no}: {line.strip()}")
    assert offenders == []
    assert {"conversations.join", "conversations.invite"} <= FORBIDDEN_METHODS


async def test_b_runtime_paths_never_join(make_core, world, hooks):
    from slackfix import payload

    core = await make_core()
    rt = core.runtimes["private"]
    assert sorted(rt.clients) == ["user"] and rt.bot_user_id is None
    await rt.poll_channel("CPRIV0001")
    await rt.poll_channel("CPRIV0001")
    p = payload("b_user_event_watched.json")
    await core.handle_socket("private", p["type"], p["payload"])
    res = await core.gate.submit(core.config.grant("full"), "private", "user", "post_message", {"channel": "CPRIV0001", "text": "x"})
    from switchboard.slack.writes import Actor

    await core.gate.decide(res["request_id"], True, Actor("admin", "owner"))
    b_calls = {n for t, n, _ in world.calls if t == "xoxp-b"}
    assert not {n for n in b_calls if "join" in n or "invite" in n}
    assert "reactions_add" not in b_calls


async def test_b_refuses_bot_token_for_user_identity(make_core, world):
    from slackfix import AUTH, ENV

    AUTH["xoxb-sneaky"] = {"ok": True, "team_id": "TBBBB2222", "user_id": "UBOTX", "bot_id": "BX", "url": "https://b.slack.com/"}
    try:
        core = await make_core(env={**ENV, "B_USER": "xoxb-sneaky"})
        rt = core.runtimes["private"]
        assert rt.clients == {} and "bot token" in rt.errors[0]
        assert core.status()["status"] == "degraded"
    finally:
        AUTH.pop("xoxb-sneaky")
