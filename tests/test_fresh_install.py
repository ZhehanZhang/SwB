"""Fresh configuration smoke tests, runnable against a built wheel with no external services."""

import asyncio
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import yaml
from conftest import (
    CAPTAIN,
    CAPTAIN2,
    CAPY_KEY,
    OWNER,
    PUBLIC,
    STATIC_KEY,
    access_verifier,
    call,
    payload,
)
from PIL import Image
from starlette.testclient import TestClient
from test_discord import (
    APP,
    CHANNEL,
    GUILD,
    MOCK_TOKEN,
    USER,
    DiscordAPI,
    Socket,
    Sockets,
    ready,
)
from test_telegram import BOT_NAME, GROUP, OWNER_ID, TOKEN, FakeBotAPI, until, update

from switchboard.app import create_app
from switchboard.core import Switchboard


@pytest.fixture
def fresh_config(tmp_path):
    def write(*, access=True, **blocks):
        path = tmp_path / "switchboard.yaml"
        path.write_text(yaml.safe_dump({
            "version": 2,
            "public_url": PUBLIC,
            **({"auth": {
                "owner_emails": ["owner@example.test"],
                "cloudflare_access": {"team_domain": "access.example.test", "aud": "test"},
            }} if access else {}),
            "blocks": {"mcp": {"use": "mcp"}, **blocks},
        }))
        return path

    return write


def connect(client, platform):
    result = payload(call(client, STATIC_KEY, "connect", {"what": platform}))
    assert result["what"] == platform
    return result


def save_token(client, platform, token):
    [step] = connect(client, platform)["next"]
    assert step["by"] == "person"
    assert step["link"].startswith(f"{PUBLIC}/claim/")
    path = urlsplit(step["link"]).path
    auth = {"Authorization": f"Bearer {STATIC_KEY}"}
    denied = client.post(path, data={"token": token})
    assert denied.status_code in (401, 403)
    if denied.status_code == 401:
        assert denied.json() == {"error": "signed_out"}
    assert client.post(path, headers={"Authorization": "Bearer not-the-owner-key"}, data={"token": token}).status_code == 403
    assert client.post(path, headers=auth, data={"token": "invalid"}).status_code == 400
    response = client.post(path, headers=auth, data={"token": token})
    assert response.status_code == 200 and token not in response.text


def test_discord_and_telegram_onboard_without_tokens_or_ids(fresh_config, tmp_path):
    path = fresh_config(
        access=False,
        discord={"use": "discord", "spaces": {"home": {}}},
        telegram={"use": "telegram", "spaces": {"home": {}}},
    )
    socket = Socket()
    sockets = Sockets(socket)
    discord = DiscordAPI()
    telegram = FakeBotAPI()
    core = Switchboard(
        path, tmp_path / "data", env={"SWITCHBOARD_KEY_OWNER": STATIC_KEY},
        block_options={
            "discord": {"transport": httpx.MockTransport(discord.handler), "gateway_connect": sockets.connect},
            "telegram": {"transport": httpx.MockTransport(telegram.handler)},
        },
    )
    block = core.blocks["discord"]

    async def start_gateway():
        identify = await asyncio.wait_for(socket.outgoing.get(), timeout=3)
        assert identify["op"] == 2 and identify["d"]["token"] == MOCK_TOKEN
        socket.feed(0, ready(guilds=[]), t="READY", s=1)
        await until(lambda: block.connected() and block.app_owner_ids == {USER})

    async def join_server():
        socket.feed(0, {"id": GUILD, "name": "Home", "owner_id": USER, "channels": [discord.channels[CHANNEL]]},
                    t="GUILD_CREATE", s=2)
        await until(lambda: block.space_for(GUILD) == "home")

    with TestClient(create_app(core), base_url=PUBLIC) as client:
        assert not discord.calls and not telegram.calls
        save_token(client, "discord", MOCK_TOKEN)
        client.portal.call(start_gateway)
        assert ("GET", "/oauth2/applications/@me", None) in discord.calls
        before_join = connect(client, "discord")
        invite = next(step["do"].split(": ", 1)[1] for step in before_join["next"]
                      if step["do"].startswith("Invite the bot to a server you own for space home: "))
        assert urlsplit(invite).netloc == "discord.com"
        query = parse_qs(urlsplit(invite).query)
        assert query["client_id"] == [APP] and "guild_id" not in query

        client.portal.call(join_server)
        status = payload(call(client, STATIC_KEY, "discord_status"))
        assert status["connected"] and status["missing"] == []
        assert status["setup"]["token_source"] == "stored"
        assert status["discovered"]["application_owner_ids"] == [USER]
        assert [(g["id"], g["space"]) for g in status["discovered"]["guilds"]] == [(GUILD, "home")]
        assert status["spaces"]["home"]["owners"] == 1
        connected = connect(client, "discord")
        assert connected["state"] == "connected"
        assert connected["next"] == [{
            "by": "person",
            "do": "Set blocks.discord.avatar to an image file to give the bot an icon.",
            "optional": True,
        }]

        save_token(client, "telegram", TOKEN)
        client.portal.call(until, core.blocks["telegram"].connected)
        client.portal.call(telegram.deliver, update(1, chat=OWNER_ID, kind="private"), update(2, mention=True))
        status = payload(call(client, STATIC_KEY, "telegram_status"))
        assert status["connected"] and status["setup"]["token_source"] == "stored"
        assert status["setup"]["bot_url"] == f"https://t.me/{BOT_NAME}"
        assert {(seen["id"], seen["type"]) for seen in status["setup"]["seen_chats"]} == {
            (OWNER_ID, "private"), (GROUP, "supergroup"),
        }


def test_captain_and_bundled_avatars_without_slack_or_avatar_dirs(fresh_config, tmp_path, capy):
    path = fresh_config(capy={"use": "capy"})
    colors = {"first": (240, 10, 20), "second": (20, 240, 10), "changed": (10, 20, 240)}
    asset_calls = []

    def preset(name):
        return {"kind": "preset", "value": name, "background": "#222222"}

    def asset(request):
        assert request.url.host == "capy.ai" and "Authorization" not in request.headers
        name = request.url.path.removeprefix("/avatars/").removesuffix(".png")
        asset_calls.append(name)
        image = BytesIO()
        Image.new("RGBA", (16, 16), (*colors[name], 255)).save(image, "PNG")
        return httpx.Response(200, content=image.getvalue(), headers={"Content-Type": "image/png"})

    capy.captains[0]["avatar"] = preset("first")
    capy.captains[1]["avatar"] = preset("second")
    core = Switchboard(
        path, tmp_path / "data", env={"CAPY_API_KEY": CAPY_KEY, "SWITCHBOARD_KEY_OWNER": STATIC_KEY},
        access_verifier=access_verifier, http_transport=httpx.MockTransport(asset),
        block_options={"capy": {"transport": httpx.MockTransport(capy.handler)}},
    )

    def captains(client):
        out = payload(call(client, STATIC_KEY, "targets", {"block": "capy", "refresh": True}))
        assert not out["errors"], out["errors"]
        response = client.get("/api/targets", headers=OWNER)
        assert response.status_code == 200, response.text
        assert {row["id"]: row["avatar_url"] for row in response.json()["targets"] if row["block"] == "capy"} == {
            row["id"]: row["avatar_url"] for row in out["targets"]
        }
        return {row["id"]: row for row in out["targets"]}

    def check_avatar(client, row, color):
        name = row["persona"]["avatar_file"]
        url = urlsplit(row["avatar_url"])
        assert url._replace(query="").geturl() == f"{PUBLIC}/avatars/{name}"
        file = core.find_avatar(name)
        assert file == core.avatar_cache / name
        response = client.get(row["avatar_url"])
        assert response.status_code == 200 and response.headers["content-type"] == "image/png"
        assert response.content == file.read_bytes()
        with Image.open(BytesIO(response.content)) as image:
            assert image.convert("RGBA").getpixel((0, 0)) == (*color, 255)
        return row["avatar_url"]

    with TestClient(create_app(core), base_url=PUBLIC) as client:
        assert client.get("/api/targets").status_code == 403
        rows = captains(client)
        assert set(rows) == {CAPTAIN, CAPTAIN2}
        first = check_avatar(client, rows[CAPTAIN], colors["first"])
        second = check_avatar(client, rows[CAPTAIN2], colors["second"])
        assert first != second
        assert sorted(asset_calls) == ["first", "second"]

        capy.captains[0]["avatar"] = preset("changed")
        changed = captains(client)
        assert changed[CAPTAIN]["persona"]["avatar_file"] != rows[CAPTAIN]["persona"]["avatar_file"]
        assert check_avatar(client, changed[CAPTAIN], colors["changed"]) != first
        assert check_avatar(client, changed[CAPTAIN2], colors["second"]) == second
        assert sorted(asset_calls) == ["changed", "first", "second"]

        for name in ("chatgpt.png", "claude.png", "claude-code.png", "codex.png", "gemini.png"):
            response = client.get(f"/avatars/{name}")
            assert response.status_code == 200, name
            assert response.headers["content-type"] == "image/png"
            assert response.content == core.find_avatar(name).read_bytes()
            with Image.open(BytesIO(response.content)) as image:
                image.verify()
