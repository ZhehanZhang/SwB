import pytest

from slackfix import ENV, base_config
from switchboard.slack.config import parse_config
from switchboard.slack.personas import PersonaError, persona_post_args, resolve_persona
from slackfix import clock, hooks, make_core, world  # noqa: F401  (fixtures)


@pytest.fixture
def cfg():
    return parse_config(base_config(), ENV)


def test_resolution_order(cfg):
    ws = cfg.workspaces["a2a"]
    full = cfg.grant("full")
    assert resolve_persona(cfg, ws, "bot", full, "gate").id == "gate"
    assert resolve_persona(cfg, ws, "bot", full, None).id == "capy"
    unbound = full.__class__(**{**full.__dict__, "persona": None})
    assert resolve_persona(cfg, ws, "bot", unbound, None).id == "gate"


def test_key_that_may_not_override(cfg):
    ws = cfg.workspaces["a2a"]
    fixed = cfg.grant("fixed")
    with pytest.raises(PersonaError, match="may not choose"):
        resolve_persona(cfg, ws, "bot", fixed, "gate")
    assert resolve_persona(cfg, ws, "bot", fixed, None).id == "capy"


def test_allowed_personas_and_unknown(cfg):
    ws = cfg.workspaces["a2a"]
    full = cfg.grant("full")
    with pytest.raises(PersonaError, match="may not use persona 'other'"):
        resolve_persona(cfg, ws, "bot", full, "other")
    free = full.__class__(**{**full.__dict__, "allowed_personas": None})
    with pytest.raises(PersonaError, match="unknown persona"):
        resolve_persona(cfg, ws, "bot", free, "ghost")


def test_user_identity_never_gets_persona(cfg):
    ws = cfg.workspaces["a2a"]
    full = cfg.grant("full")
    assert resolve_persona(cfg, ws, "user", full, None) is None
    with pytest.raises(PersonaError, match="only to bot posts"):
        resolve_persona(cfg, ws, "user", full, "gate")


def test_post_args():
    assert persona_post_args({"name": "Capy", "icon_url": "https://i/c.png", "emoji": None}) == {"username": "Capy", "icon_url": "https://i/c.png"}
    assert persona_post_args({"name": "Gate", "icon_url": None, "emoji": ":robot_face:"}) == {"username": "Gate", "icon_emoji": ":robot_face:"}
    assert persona_post_args(None) == {}


async def test_bot_post_carries_persona(make_core, world):
    core = await make_core()
    key = core.config.grant("full")
    await core.gate.submit(key, "a2a", "bot", "post_message", {"channel": "CWATCH001", "text": "hi"}, persona_id="gate")
    await core.gate.submit(key, "a2a", "bot", "post_message", {"channel": "CWATCH001", "text": "hi"})
    posts = [kw for t, n, kw in world.calls if t == "xoxb-a" and n == "chat_postMessage"]
    assert posts[0]["username"] == "Gate" and posts[0]["icon_emoji"] == ":robot_face:"
    assert posts[1]["username"] == "Capy" and posts[1]["icon_url"] == "https://img.example/capy.png"
