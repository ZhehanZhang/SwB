import pytest

from conftest import OWNER, PUBLIC, STATIC_KEY, base_config, default_env

DISCORD_TOKEN = ".".join(("a" * 24, "b" * 6, "c" * 35))
TELEGRAM_TOKEN = "123456:" + "a" * 36


@pytest.fixture
def providers(make):
    return make(
        base_config(version=2, blocks={
            "discord-bot": {
                "use": "discord", "application_id": "111", "avatar": "switchboard.png", "connect": False,
                "spaces": {
                    "engineering": {"guild_id": "201", "owner_ids": ["303"], "listen": ["501"],
                                    "project": "default", "channel_projects": {"501": "release", "#builds": "ignored", "503": None}},
                },
            },
            "telegram": {"avatar": "switchboard.png", "connect": False,
                         "spaces": {"ops": {"owner_ids": ["811"], "chats": [-100813], "listen": [-100813]}}},
        }),
        env={**default_env(), "DISCORD_BOT_TOKEN": DISCORD_TOKEN, "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN},
    )


async def _no_upload(_: bytes) -> None:
    pass


def test_discord_connection_uses_discovery_and_safe_status_fields(providers):
    core, client = providers
    bot = core.blocks["discord-bot"]
    bot.bot_name, bot.user_id, bot._gateway.ready = "Release bot", "401", True
    bot.app_owner_ids = frozenset({"301"})
    client.portal.call(bot.dispatch, "GUILD_CREATE", {
        "id": "201", "name": "Engineering", "owner_id": "302",
        "channels": [
            {"id": "501", "name": "builds", "type": 0}, {"id": "502", "name": "general", "type": 0},
            {"id": "503", "name": "off-topic", "type": 0}, {"id": "504", "name": "thread", "type": 11},
        ],
    })
    client.portal.call(bot.dispatch, "GUILD_CREATE", {
        "id": "202", "name": "Community", "owner_id": "304",
        "channels": [{"id": "505", "name": "introductions", "type": 0}],
    })
    client.portal.call(bot.apply_avatar, bot.user_id, _no_upload)
    status = bot.status()

    response = client.get("/api/connections/discord", headers=OWNER)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert DISCORD_TOKEN not in response.text and "token_source" not in response.text
    assert response.json() == {
        "platform": "discord", "block": "discord-bot", "state": "connected",
        "details": {
            "bot_name": status["setup"]["bot_name"], "invite_url": status["setup"]["invite_url"],
            "owner_ids": ["301", "303"],
            "avatar": {"file": "switchboard.png", "state": "applied", "url": "/api/connections/discord/avatar"},
            "guilds": [
                {"id": "201", "name": "Engineering", "space": "engineering", "owner_id": "302", "channels": [
                    {"id": "501", "name": "builds", "project": "release", "listening": True},
                    {"id": "502", "name": "general", "project": "default", "listening": False},
                    {"id": "503", "name": "off-topic", "project": None, "listening": False},
                ]},
                {"id": "202", "name": "Community", "space": None, "owner_id": "304", "channels": [
                    {"id": "505", "name": "introductions", "project": None, "listening": False},
                ]},
            ],
        },
    }


def test_telegram_connection_uses_seen_chats_and_actual_spaces(providers):
    core, client = providers
    bot = core.blocks["telegram"]
    bot._bot = {"id": 810, "name": None, "username": "ReleaseSwbBot"}
    for msg in [
        {"message_id": 1, "chat": {"id": 811, "type": "private"}, "from": {"id": 811}},
        {"message_id": 2, "chat": {"id": -100812, "type": "supergroup", "title": "Release Ops"}, "from": {"id": 812}},
    ]:
        client.portal.call(bot._receive, msg, 810, "ReleaseSwbBot")

    response = client.get("/api/connections/telegram", headers=OWNER)
    assert response.status_code == 200
    assert TELEGRAM_TOKEN not in response.text and "token_source" not in response.text
    assert response.json() == {
        "platform": "telegram", "block": "telegram", "state": "disconnected",
        "details": {
            "bot_name": "@ReleaseSwbBot", "owner_ids": ["811"],
            "avatar": {"file": "switchboard.png", "state": "pending", "url": None},
            "chats": [
                {"id": "-100812", "title": "Release Ops", "type": "supergroup", "space": None, "listen": "unlisted"},
                {"id": "811", "title": None, "type": "private", "space": "ops", "listen": "direct"},
            ],
        },
    }
    bot._online = True
    assert client.get("/api/connections/telegram", headers=OWNER).json()["state"] == "connected"


@pytest.mark.parametrize("platform", ["discord", "telegram", "slack"])
def test_connection_routes_require_owner_and_list_unconfigured(make, platform):
    _, client = make(base_config(version=2, blocks={}))
    route = f"/api/connections/{platform}"
    for url in (route, f"{route}/avatar"):
        for headers in ({}, {"Authorization": f"Bearer {STATIC_KEY}"}):
            denied = client.get(url, headers=headers)
            assert denied.status_code == 401 and denied.json() == {"error": "signed_out"}
            assert denied.headers["cache-control"] == "no-store"
    assert client.get(route, headers=OWNER).json() == {"platform": platform, "block": None, "state": "not_configured", "details": None}
    assert client.get(f"{route}/avatar", headers=OWNER).status_code == 404
    csrf = client.get("/auth/session", headers=OWNER).json()["csrf"]
    assert client.post(route, headers={**OWNER, "origin": PUBLIC, "x-csrf-token": csrf}).status_code == 405
    assert client.get("/api/connections/github", headers=OWNER).status_code == 404


def test_slack_connection_summary_uses_runtime_status(make):
    core, client = make(base_config(version=2, blocks={
        "slack": {"workspaces": {"ops": {"team_id": "TDEMO01234", "intake": "poll",
                                         "identities": {"bot": {"token_env": "SLACK_TEST_BOT_TOKEN"}}}}},
    }))
    core.slack.runtimes["ops"].connected = True
    response = client.get("/api/connections/slack", headers=OWNER)
    assert response.status_code == 200
    assert response.json() == {
        "platform": "slack", "block": "slack", "state": "connected",
        "details": {"workspaces": [{"name": "ops", "intake": "poll", "connected": True}]},
    }


@pytest.mark.parametrize("platform", ["discord", "telegram"])
def test_avatar_serves_only_the_applied_image(providers, platform, tmp_path, monkeypatch):
    core, client = providers
    bot = core.blocks["discord-bot" if platform == "discord" else "telegram"]
    if platform == "discord":
        bot.user_id = "401"
        bot_id = bot.user_id
    else:
        bot._bot = {"id": 810, "username": "ReleaseSwbBot", "name": "Release bot"}
        bot_id = bot._bot["id"]
    route = f"/api/connections/{platform}/avatar"
    assert client.get(route, headers=OWNER).status_code == 404

    client.portal.call(bot.apply_avatar, bot_id, _no_upload)
    response = client.get(route, headers=OWNER)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")

    changed = tmp_path / "switchboard.png"
    changed.write_bytes(response.content + b"changed")
    monkeypatch.setattr(core, "find_avatar", lambda _: changed)
    assert client.get(route, headers=OWNER).status_code == 404
