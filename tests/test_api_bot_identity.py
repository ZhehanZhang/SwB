import base64
import json
from io import BytesIO

import httpx
from PIL import Image

from conftest import OWNER, PUBLIC, STATIC_KEY, base_config, default_env
from switchboard.bot_identity import MAX_ICON_BYTES

DISCORD = "/api/bot-identities/product-discord"
TELEGRAM = "/api/bot-identities/telegram"


def fixture_config():
    return base_config(version=2, blocks={
        "mcp": {},
        "product-discord": {"use": "discord", "connect": False, "spaces": {"product": {"guild_id": "1000"}}},
        "telegram": {"use": "telegram", "connect": False, "spaces": {"team": {"chats": [-10000]}}},
    })


def headers(client):
    return {**OWNER, "Origin": PUBLIC, "X-CSRF-Token": client.get("/auth/session", headers=OWNER).json()["csrf"]}


def picture(*, size=(650, 300), format="WEBP"):
    file = BytesIO()
    Image.new("RGB", size, "#935dfa").save(file, format=format)
    return base64.b64encode(file.getvalue()).decode()


def test_identity_routes_require_owner_session_and_csrf(make):
    _, client = make(fixture_config(), env=default_env())
    for method in ("GET", "POST"):
        for unauthorized in ({}, {"Authorization": f"Bearer {STATIC_KEY}"}):
            result = client.request(method, DISCORD, headers=unauthorized)
            assert result.status_code == 401 and result.headers["cache-control"] == "no-store"
    for unauthorized in (OWNER, {**headers(client), "Origin": "https://other.example.test"}):
        result = client.post(DISCORD, headers=unauthorized, json={"name": "New name"})
        assert result.status_code == 403
    assert client.get("/api/bot-identities/mcp", headers=OWNER).status_code == 404
    assert client.post(TELEGRAM, headers=headers(client), json={"space": "team", "name": "Team"}).status_code == 403


def test_discord_save_inherit_and_reset_identity(make):
    calls = []

    def api(request):
        assert request.method == "PATCH"
        payload = json.loads(request.content)
        calls.append((request.url.path, payload))
        return httpx.Response(200, json={"username": payload.get("username")})

    env = {**default_env(), "DISCORD_BOT_TOKEN": "fixture-discord-token"}
    core, client = make(fixture_config(), env=env)
    core.blocks["product-discord"]._transport = httpx.MockTransport(api)
    signed = headers(client)
    first = client.get(DISCORD, headers=OWNER).json()
    assert first["identity"] == {
        "name": "SwB", "icon_url": "/avatars/swb.png", "can_edit": True, "can_edit_icon": True,
        "permissions_url": None, "overridden": False,
    }
    saved = client.post(DISCORD, headers=signed, json={"name": "Release desk", "icon": picture()})
    assert saved.status_code == 200, saved.text
    snapshot = saved.json()
    icon = snapshot["identity"]["icon_url"]
    assert snapshot["identity"]["overridden"] and icon.startswith("/avatars/bot-")
    avatar = client.get(icon)
    assert avatar.status_code == 200 and avatar.headers["content-type"] == "image/png"
    with Image.open(BytesIO(avatar.content)) as image:
        assert image.format == "PNG" and image.size == (512, 512)
    assert snapshot["spaces"][0]["identity"]["icon_url"] == icon
    assert calls[0] == ("/api/v10/users/@me", {
        "username": "Release desk", "avatar": "data:image/png;base64," + base64.b64encode(avatar.content).decode(),
    })
    guild = client.post(DISCORD, headers=signed, json={"space": "product", "name": "Bugs"})
    assert guild.status_code == 200, guild.text
    assert guild.json()["spaces"][0]["identity"]["name"] == "Bugs"
    assert calls[-1][0] == "/api/v10/guilds/1000/members/@me"
    assert calls[-1][1]["nick"] == "Bugs"
    reset = client.post(DISCORD, headers=signed, json={"space": "product", "reset": True})
    assert reset.status_code == 200 and reset.json()["spaces"][0]["identity"]["name"] == "Release desk"
    assert calls[-1][1] == {"nick": None, "avatar": None}
    assert core.bot_identities.get("product-discord", "product") is None
    defaults = client.post(DISCORD, headers=signed, json={"reset": True})
    assert defaults.status_code == 200 and defaults.json()["identity"] == first["identity"]
    assert calls[-1][1]["username"] == "SwB"


def test_bot_identity_validates_icon_and_keeps_saved_state_on_provider_error(make):
    def api(request):
        return httpx.Response(403)

    env = {**default_env(), "DISCORD_BOT_TOKEN": "fixture-discord-token"}
    core, client = make(fixture_config(), env=env)
    core.blocks["product-discord"]._transport = httpx.MockTransport(api)
    signed = headers(client)
    for icon in ("not an image", picture(format="GIF"), picture(size=(2049, 100)),
                 base64.b64encode(b"x" * (MAX_ICON_BYTES + 1)).decode()):
        response = client.post(DISCORD, headers=signed, json={"icon": icon})
        assert response.status_code == 400 and response.json() == {"error": "invalid_icon"}
    assert client.post(DISCORD, headers=signed, json={"space": "unknown", "name": "New name"}).status_code == 400
    assert client.post(DISCORD, headers=signed, json={"name": "x"}).status_code == 400
    result = client.post(DISCORD, headers=signed, json={"name": "Release bot"})
    assert result.status_code == 502 and result.json() == {"error": "identity_not_saved"}
    assert core.bot_identities.get("product-discord") is None
