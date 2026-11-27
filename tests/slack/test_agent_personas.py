"""Automatic per-agent personas, avatar files with versioned URLs, watching every channel the bot is in, and
per-workspace approvers."""

import copy
import re

import pytest

from slackfix import ENV, OWNER_A, base_config, payload
from slackfix import clock, hooks, make_core, world  # noqa: F401  (fixtures)
from switchboard.slack.config import ConfigError, parse_config
from switchboard.slack.engine import BUNDLED_AVATARS
from switchboard.slack.events import ClassifyContext, classify
from switchboard.slack.personas import resolve_persona
from switchboard.slack.writes import Actor, WriteError

PUBLIC = "https://sb.example.test"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def agent_config():
    c = copy.deepcopy(base_config())
    c["public_base_url"] = PUBLIC
    c["personas"].update({"claude": {"name": "Claude"}, "helperbot": {"name": "HelperBot"}, "switchboard": {"name": "Switchboard"}})
    c["workspaces"]["a2a"]["default_persona"] = "switchboard"
    c["agents"].update({
        "claude": {"workspaces": {"a2a": ["bot"]}},
        "capy": {"persona_by_workspace": {"a2a": "helperbot"}, "allow_persona_override": True, "allowed_personas": ["helperbot", "capy"],
                 "workspaces": {"a2a": ["bot", "user"]}},
        "stranger": {"workspaces": {"a2a": ["bot"]}},
    })
    return c


def test_persona_follows_the_agent_like_send_keys():
    cfg = parse_config(agent_config(), ENV)
    ws = cfg.workspaces["a2a"]
    assert resolve_persona(cfg, ws, "bot", cfg.grant("claude"), None).name == "Claude"
    assert resolve_persona(cfg, ws, "bot", cfg.grant("capy"), None).name == "HelperBot"
    assert resolve_persona(cfg, ws, "bot", cfg.grant("capy"), "capy").name == "Capy"
    assert resolve_persona(cfg, ws, "bot", cfg.grant("stranger"), None).name == "Switchboard"
    assert resolve_persona(cfg, ws, "user", cfg.grant("capy"), None) is None


def test_avatar_defaults_to_persona_file_and_bad_mapping_refused():
    cfg = parse_config(agent_config(), ENV)
    assert cfg.personas["claude"].avatar == "claude.png" and cfg.personas["claude"].icon_url is None
    assert cfg.personas["gate"].avatar == "gate.png" and cfg.personas["gate"].emoji == ":robot_face:"
    assert cfg.personas["capy"].avatar is None
    later = agent_config()
    later["agents"]["capy"]["persona_by_workspace"] = {"a2a": "ghost"}
    cfg2 = parse_config(later, ENV)
    assert resolve_persona(cfg2, cfg2.workspaces["a2a"], "bot", cfg2.grant("capy"), None).name == "Capy"
    from switchboard.slack.config import Persona
    assert resolve_persona(cfg2, cfg2.workspaces["a2a"], "bot", cfg2.grant("capy"), None, {**cfg2.personas, "ghost": Persona("ghost", "Ghost")}).name == "Ghost"
    bad = agent_config()
    bad["agents"]["capy"]["persona_by_workspace"] = {"nowhere": "capy"}
    with pytest.raises(ConfigError, match="unknown workspace"):
        parse_config(bad, ENV)


async def test_bot_posts_use_the_agent_persona_and_avatar_file(make_core, world, tmp_path):
    overlay = tmp_path / "branding"
    overlay.mkdir()
    (overlay / "claude.png").write_bytes(PNG)
    c = agent_config()
    c["avatar_dirs"] = [str(overlay)]
    core = await make_core(c)
    await core.gate.submit(core.config.grant("claude"), "a2a", "bot", "post_message", {"channel": "CWATCH001", "text": "hi"})
    await core.gate.submit(core.config.grant("capy"), "a2a", "bot", "post_message", {"channel": "CWATCH001", "text": "hi"})
    await core.gate.submit(core.config.grant("stranger"), "a2a", "bot", "post_message", {"channel": "CWATCH001", "text": "hi"})
    posts = [kw for t, n, kw in world.calls if t == "xoxb-a" and n == "chat_postMessage"]
    assert posts[0]["username"] == "Claude" and re.fullmatch(PUBLIC + r"/avatars/claude\.png\?v=\d+", posts[0]["icon_url"])
    assert posts[1]["username"] == "HelperBot" and re.fullmatch(PUBLIC + r"/avatars/swb\.png\?v=\d+", posts[1]["icon_url"])
    assert posts[2]["username"] == "Switchboard"
    (core.avatar_dir).mkdir(parents=True, exist_ok=True)
    (core.avatar_dir / "claude.png").write_bytes(PNG + b"new")
    assert core.find_avatar("claude.png") == core.avatar_dir / "claude.png"
    assert core.find_avatar("../x.png") is None


def test_bundled_monograms_cover_the_known_agents():
    for name in ("swb", "claude", "chatgpt", "gemini", "codex", "claude-code", "capy"):
        data = (BUNDLED_AVATARS / f"{name}.png").read_bytes()
        assert data.startswith(b"\x89PNG") and len(data) > 1000


def test_watch_all_marks_every_channel_message_but_not_dms():
    ctx = ClassifyContext(has_bot=True, bot_user_id="UB", bot_id="BB", watch_all=True)
    assert classify({"type": "message", "user": "U1", "channel": "CANY", "channel_type": "channel", "ts": "1"}, ctx) == "watched"
    assert classify({"type": "message", "user": "U1", "channel": "GANY", "channel_type": "group", "ts": "1"}, ctx) == "watched"
    assert classify({"type": "message", "user": "U1", "channel": "DANY", "channel_type": "im", "ts": "1"}, ctx) == "dm"
    assert classify({"type": "message", "user": "U1", "channel": "CANY", "channel_type": "channel", "ts": "1"}, ClassifyContext(has_bot=True)) is None
    c = base_config()
    c["workspaces"]["a2a"]["watched"] = ["*", "#more"]
    ws = parse_config(c, ENV).workspaces["a2a"]
    assert ws.watch_all and ws.watched == ("#more",)


async def test_workspace_approver_gets_the_dm_and_may_decide(make_core, world):
    c = base_config()
    c["workspaces"]["a2a"]["approver_user_id"] = "UHERE0001"
    core = await make_core(c)
    req = await core.gate.submit(core.config.grant("full"), "a2a", "user", "post_message", {"channel": "CWATCH001", "text": "x"})
    opened = [kw for t, n, kw in world.calls if n == "conversations_open"]
    assert opened[-1]["users"] == "UHERE0001"
    with pytest.raises(WriteError, match="approver"):
        await core.gate.decide(req["request_id"], True, Actor("slack", "UHERE0001", "private"))
    p = payload("a_block_actions_approve.json")
    p["payload"]["actions"][0]["value"] = req["request_id"]
    p["payload"]["user"]["id"] = "UHERE0001"
    await core.handle_socket("a2a", p["type"], p["payload"])
    got = core.store.get_request(req["request_id"])
    assert got["status"] == "sent" and got["decided_by"] == "slack:UHERE0001"
    assert ("a2a", OWNER_A) in core.config.approvers()
