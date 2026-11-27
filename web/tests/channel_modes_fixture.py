import asyncio
import os
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import httpx
import uvicorn
import yaml
from PIL import Image, ImageDraw

from switchboard import pages
from switchboard.app import create_app
from switchboard.blocks.discord import Channel
from switchboard.claims import Field
from switchboard.core import Switchboard

HOST = "127.0.0.1"
PORT = int(os.environ.get("SWB_PLAYWRIGHT_PORT", "4178"))
CAPTAIN = "jam_EXAMPLEATLAS"
SLACK_TEAM_ID = "T000EXAMPLE"


class SlackFixture:
    async def auth_test(self) -> dict:
        return {"ok": True, "team_id": SLACK_TEAM_ID, "user_id": "U000EXAMPLE", "bot_id": "B000EXAMPLE"}

    async def users_info(self, *, user: str) -> dict:
        return {"ok": True, "user": {"id": user, "name": "SwB"}}

    async def users_conversations(self, **kwargs) -> dict:
        return {"ok": True, "channels": []}


async def verify_access(assertion: str) -> str | None:
    return "owner@example.test" if assertion == "channel-mode-fixture" else None


async def capy_fixture(request: httpx.Request) -> httpx.Response:
    if request.url.host == "swb.example.com":
        if request.url.path == "/api/v1/captains":
            return httpx.Response(200, json={"items": [{
                "id": CAPTAIN, "name": "Atlas", "authorId": "user_EXAMPLE",
                "avatar": {"kind": "preset", "value": "atlas", "image": "https://swb.example.com/atlas.png"},
            }]})
        if request.url.path == "/api/v1/users":
            return httpx.Response(200, json={"items": [{"id": "user_EXAMPLE", "firstName": "Alex", "lastName": "Rivera"}]})
        if request.url.path == "/atlas.png":
            image = Image.new("RGB", (512, 512), "#132b42")
            draw = ImageDraw.Draw(image)
            draw.ellipse((60, 60, 452, 452), fill="#50cea2")
            draw.polygon([(256, 105), (408, 365), (104, 365)], fill="#174563")
            output = BytesIO()
            image.save(output, format="PNG")
            return httpx.Response(200, content=output.getvalue())
    return httpx.Response(404)


async def seed_inbox(core: Switchboard) -> None:
    for i in range(1, 27):
        handle = core.inbox.open_handle(
            handle=f"h_history_{i}", owner="claude-code", destination="mailbox", ref=f"history-{i}",
            project="operations" if i <= 22 else "community", title=f"Conversation {i}", meta={},
        )
        if i == 1:
            await core.inbox.add(handle, "message", {"text": "The first conversation."})
            core.store.x("INSERT INTO conversation_turns(root, turns, paused_at) VALUES (?, ?, ?)", (handle, 200, core.clock()))
    pairing = core.connections.mint(agent="claude-code", project="operations", client=None, by="owner")
    key = core.connections.redeem(pairing["code"])
    core.slack.store.create_request(
        workspace="studio", identity="bot", action="post_message", params={
            "channel": "planning", "text": "The release checklist is ready for review.",
        }, persona=None, key_id=key["key_id"], ttl_seconds=3600,
    )
    handle = core.inbox.open_handle(
        handle="h_example_release", owner="claude-code", destination="capy", ref=CAPTAIN, project="operations",
        title="Release preparation", meta={}, target=f"capy:captain:{CAPTAIN}",
    )
    await core.inbox.add(handle, "message", {
        "text": "Please review the release checklist.", "from": {"kind": "agent", "agent": "claude-code", "name": "Claude Code"},
    })
    await core.inbox.add(handle, "reply", {
        "text": "The checklist is ready to share.", "from": core.blocks["capy"]._from(CAPTAIN, {"source": "assistant"}),
    })
    core.inbox.update_handle(handle, status="completed", open=False)


def run(path: Path) -> None:
    config = path / "switchboard.yaml"
    config.write_text(yaml.safe_dump({
        "version": 2,
        "public_url": f"http://localhost:{PORT}",
        "auth": {"owner_emails": ["owner@example.test"], "cloudflare_access": {
            "team_domain": "access.example.test", "aud": "channel-mode-fixture",
        }},
        "blocks": {
            "mcp": {"use": "mcp"},
            "capy": {"use": "capy", "api_base": "https://swb.example.com/api/v1"},
            "muse": {"use": "muse"},
            "team-chat": {"use": "discord", "connect": False, "spaces": {"engineering": {
                "guild_id": "1000", "project": "operations", "channels": {
                    "2001": "assist", "2002": {"mode": "off", "project": "community"},
                },
            }}},
            "slack": {"use": "slack", "public_base_url": "https://swb.example.com",
                      "personas": {"example": {"name": "Review bot", "avatar": "review-bot.png"}},
                      "workspaces": {"studio": {
                          "team_id": SLACK_TEAM_ID, "identities": {"bot": {"token_env": "EXAMPLE_SLACK_TOKEN"}},
                      }},
                      "spaces": {"studio": {"channels": {"#planning": "watch"}}}},
            "telegram": {"use": "telegram", "connect": False, "spaces": {"dispatch": {
                "chats": [-1001000], "mode": "watch",
            }}},
            "repo-events": {"use": "github", "spaces": {"release": {
                "repo": "example-org/example-repo", "project": "operations", "mode": "triage",
            }}},
        },
    }))
    transport = httpx.MockTransport(capy_fixture)
    core = Switchboard(config, path / "data",
                       env={"CAPY_API_KEY": "example-capy-fixture", "EXAMPLE_SLACK_TOKEN": "example-slack-fixture"},
                       access_verifier=verify_access, block_options={"capy": {"transport": transport}}, http_transport=transport,
                       slack_options={"connect": False, "client_factory": lambda _token: SlackFixture()})
    core.projects.create(slug="operations", name="Operations")
    core.projects.create(slug="community", name="Community")
    core.memory.write(source="claude-code", scope="general", namespace="notes", key="release-plan",
                      title="Release plan", body="Prepare the release.", confirm_general=True)
    core.memory.write(source="claude-code", scope="general", namespace="notes",
                      title="Meeting notes", body="Review the schedule.", confirm_general=True)
    discord = core.blocks["team-chat"]
    discord.guilds.add("1000")
    discord.guild_info["1000"] = {"name": "Engineering", "owner_id": "1001"}
    discord.channels["2001"] = Channel(None, "deployments", 0, "1000")
    discord.channels["2002"] = Channel(None, "community", 0, "1000")
    core.blocks["telegram"]._seen[-1001000] = {"id": -1001000, "title": None, "type": "supergroup"}
    core.modes.watch("team-chat", "engineering", "2001", "msg-example", "1001", "Alex",
                     "Release ready", None, None, project="operations")
    core.audit.record(action="webhook", origin="repo-events", outcome="dropped", destination="repo-events", source="github",
                      client="local", target="example-org/example-repo", detail="channel mode off")
    asyncio.run(seed_inbox(core))
    app = create_app(core)
    base = f"http://localhost:{PORT}"
    fixtures = {
        "/fixture/token": urlsplit(core.claims.mint("DISCORD_BOT_TOKEN", base)).path,
        "/fixture/muse": urlsplit(core.claims.mint("MUSE_BRIDGE_TOKEN", base)).path,
        "/fixture/credentials": urlsplit(core.claims.mint_group(
            "GitHub App", [Field("EXAMPLE_APP_ID", "App ID", "id"), Field("EXAMPLE_PRIVATE_KEY", "Private key", "pem")],
            base, provider="github",
        )).path,
    }

    async def service(scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/fixture/login":
            return await pages.login_page({"passkeys": True, "oidc": None}, "/", None)(scope, receive, send)
        if scope["type"] == "http" and scope["path"] in fixtures:
            headers = tuple((key, value) for key, value in scope["headers"] if key != b"cf-access-jwt-assertion")
            scope = {**scope, "path": fixtures[scope["path"]],
                     "headers": (*headers, (b"cf-access-jwt-assertion", b"channel-mode-fixture"))}
        await app(scope, receive, send)

    uvicorn.run(service, host=HOST, port=PORT, access_log=False)


if __name__ == "__main__":
    with TemporaryDirectory(prefix="swb-channel-modes-") as directory:
        run(Path(directory))
