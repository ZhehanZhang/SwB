"""Slug routing: '@bot <slug> text' picks a Captain, agent or project; threads stick to their target."""

import asyncio
import copy
import io

import httpx
import pytest

from slackfix import BOT_USER, ENV, OWNER_A, base_config
from slackfix import clock, hooks, make_core, world  # noqa: F401  (fixtures)
from switchboard.slack.capy_avatars import render_captain
from switchboard.slack.config import Persona, parse_config
from switchboard.slack.engine import BUNDLED_AVATARS
from switchboard.slack.routing import build_targets, near, split_slug

CAPTAINS = [{"id": "jam_P", "name": "HelperBot"}, {"id": "jam_C", "name": "Captain", "code": "ops-captain"}, {"id": "jam_X", "name": "Claude"},
            {"id": "jam_old", "name": "Gone", "archived": True}]
AGENTS = ["claude", "chatgpt", "slack", "codex"]
PROJECTS = [{"slug": "orbit", "name": "Orbit"}, {"slug": "codex", "name": "Codex project"}]


def targets():
    return build_targets(CAPTAINS, AGENTS, PROJECTS, {"pp": "helperbot"})


def test_targets_found_automatically_with_collision_order_and_aliases():
    t = targets()
    assert t["helperbot"].kind == "captain" and t["helperbot"].id == "jam_P" and t["helperbot"].persona == "helperbot"
    assert t["claude"].kind == "captain"
    assert t["codex"].kind == "agent"
    assert t["orbit"].kind == "project"
    assert t["chatgpt"].kind == "agent" and "slack" not in t and "gone" not in t
    assert t["pp"].id == "jam_P" and t["pp"].kind == "captain"
    assert "captain" not in t


def test_split_slug():
    assert split_slug(f"<@{BOT_USER}> HelperBot: deploy status?", BOT_USER) == ("helperbot", "deploy status?")
    assert split_slug(f"<@{BOT_USER}>   hello there", BOT_USER) == ("hello", "there")
    assert split_slug(f"<@{BOT_USER}>", BOT_USER) == ("", "")
    assert near("helperbo", targets()) == ["helperbot"] and near("hello", targets()) == []


def cfg(**ws):
    c = copy.deepcopy(base_config())
    c["workspaces"]["a2a"].update(ws)
    return c


def mention(ts, text, thread_ts=None):
    e = {"type": "app_mention", "user": OWNER_A, "channel": "CPUBLIC01", "channel_type": "channel", "ts": ts, "text": text}
    if thread_ts:
        e["thread_ts"] = thread_ts
    return e


async def test_slug_match_no_slug_and_thread_stickiness(make_core, world):
    core = await make_core(cfg(default_target="ops-captain"))
    core.set_targets(targets())
    a = await core.ingest("a2a", mention("1700000100.000001", f"<@{BOT_USER}> helperbot deploy status?"))
    assert (a["target"], a["target_kind"], a["text"]) == ("helperbot", "captain", "deploy status?")
    b = await core.ingest("a2a", mention("1700000100.000002", f"<@{BOT_USER}> what is up"))
    assert (b["target"], b["target_kind"]) == ("ops-captain", "captain") and b["text"] == f"<@{BOT_USER}> what is up"
    c = await core.ingest("a2a", mention("1700000100.000003", f"<@{BOT_USER}> and the logs?", thread_ts="1700000100.000001"))
    assert c["target"] == "helperbot"
    d = await core.ingest("a2a", mention("1700000100.000004", f"<@{BOT_USER}> chatgpt take this one", thread_ts="1700000100.000001"))
    assert d["target"] == "chatgpt" and d["target_kind"] == "agent"
    e = await core.ingest("a2a", mention("1700000100.000005", f"<@{BOT_USER}> still there?", thread_ts="1700000100.000001"))
    assert e["target"] == "chatgpt"
    assert not [n for t, n, kw in world.calls if n == "chat_postEphemeral"]
    assert core.thread_persona("a2a", "CPUBLIC01", "1700000100.000001") == "chatgpt"


async def test_unknown_slug_gets_a_hint_and_goes_to_the_default(make_core, world):
    core = await make_core(cfg(default_target="ops-captain"))
    core.set_targets(targets())
    p = await core.ingest("a2a", mention("1700000200.000001", f"<@{BOT_USER}> helperbo are you there"))
    assert p["target"] == "ops-captain" and p["text"].endswith("helperbo are you there")
    hints = [kw for t, n, kw in world.calls if n == "chat_postEphemeral"]
    assert len(hints) == 1 and "helperbot" in hints[0]["text"] and hints[0]["user"] == OWNER_A


async def test_sinks_take_only_their_targets(make_core, hooks):
    c = cfg(default_target="ops-captain")
    c["workspaces"]["a2a"]["sinks"][0]["targets"] = ["ops-captain"]
    core = await make_core(c)
    core.set_targets(targets())
    assert core.sink_accepts("a2a", await core.ingest("a2a", mention("1700000300.000001", f"<@{BOT_USER}> hi"))) == ["a"]
    assert core.sink_accepts("a2a", await core.ingest("a2a", mention("1700000300.000002", f"<@{BOT_USER}> helperbot hi"))) == []


async def test_capy_posts_in_a_routed_thread_as_the_captain(make_core, world):
    c = cfg(default_target="ops-captain")
    c["agents"]["capy"] = {"workspaces": {"a2a": ["bot"]}}
    core = await make_core(c)
    core.set_targets(targets(), {"helperbot": Persona(id="helperbot", name="HelperBot")})
    core.target_agents = lambda t: {"capy"} if t.kind == "captain" else set()
    await core.ingest("a2a", mention("1700000400.000001", f"<@{BOT_USER}> helperbot hi"))
    await core.gate.submit(core.config.grant("capy"), "a2a", "bot", "post_message", {"channel": "CPUBLIC01", "text": "hello", "thread_ts": "1700000400.000001"})
    await core.gate.submit(core.config.grant("capy"), "a2a", "bot", "post_message", {"channel": "CPUBLIC01", "text": "top level"})
    posts = [kw for t, n, kw in world.calls if n == "chat_postMessage"]
    assert posts[-2]["username"] == "HelperBot" and posts[-1]["username"] == "Capy"


async def test_ambiguous_captain_never_falls_back_to_the_slack_default(make_core, world):
    core = await make_core(cfg(default_target="ops-captain"))
    core.set_targets(targets())
    assert await core.ingest("a2a", mention("1700000401.000001", f"<@{BOT_USER}> captain check the example")) is None
    assert core.store.thread_target("a2a", "CPUBLIC01", "1700000401.000001") is None
    hints = [kw["text"] for _, name, kw in world.calls if name == "chat_postEphemeral"]
    assert hints == ["Choose a Captain by alias or full address."]
    core.set_targets(build_targets([CAPTAINS[0]], [], []))
    push = await core.ingest("a2a", mention("1700000401.000002", f"<@{BOT_USER}> captain check the example"))
    assert (push["target"], push["target_kind"]) == ("captain", "captain")


async def test_captain_avatar_preset_composited_and_monogram_fallback(tmp_path):
    from PIL import Image

    face = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    face.putpixel((256, 256), (255, 0, 0, 255))
    buf = io.BytesIO()
    face.save(buf, format="PNG")
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, content=buf.getvalue()) if req.url.path == "/avatars/capy-avatar-1.png" else httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        name = await render_captain(http, tmp_path, "HelperBot", {"kind": "preset", "value": "capy-avatar-1", "background": "#4471D0"}, True)
        img = Image.open(tmp_path / name)
        assert img.size == (512, 512) and img.getpixel((5, 5))[:3] == (0x44, 0x71, 0xD0) and img.getpixel((256, 256))[:3] == (255, 0, 0)
        again = await render_captain(http, tmp_path, "HelperBot", {"kind": "preset", "value": "capy-avatar-1", "background": "#4471D0"}, True)
        assert again == name and len(seen) == 1
        other = await render_captain(http, tmp_path, "HelperBot", {"kind": "preset", "value": "capy-avatar-9", "background": "#4471D0"}, True)
        assert other != name and Image.open(tmp_path / other).getpixel((256, 40))[:3] == (0x44, 0x71, 0xD0)
        off = await render_captain(http, tmp_path, "HelperBot", {"kind": "preset", "value": "capy-avatar-1", "background": "#4471D0"}, False)
        assert off not in (name, other) and len(seen) == 2


async def test_captains_without_avatar_specs_use_the_bundled_capy_mark(tmp_path):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("unexpected asset fetch"))) as http:
        first = await render_captain(http, tmp_path, "Captain", None, True, captain_id="jam_first")
        second = await render_captain(http, tmp_path, "Captain", {}, True, captain_id="jam_second")
    assert first != second
    assert (tmp_path / first).read_bytes() == (tmp_path / second).read_bytes() == (BUNDLED_AVATARS / "capy.png").read_bytes()
