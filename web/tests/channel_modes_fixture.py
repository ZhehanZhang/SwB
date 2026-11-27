from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import uvicorn
import yaml

from switchboard import pages
from switchboard.app import create_app
from switchboard.blocks.discord import Channel
from switchboard.claims import Field
from switchboard.core import Switchboard

HOST = "127.0.0.1"
PORT = 4178


async def verify_access(assertion: str) -> str | None:
    return "owner@example.test" if assertion == "channel-mode-fixture" else None


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
            "muse": {"use": "muse"},
            "team-chat": {"use": "discord", "connect": False, "spaces": {"engineering": {
                "guild_id": "1000", "project": "operations", "channels": {
                    "2001": "assist", "2002": {"mode": "off", "project": "community"},
                },
            }}},
            "slack": {"use": "slack",
                      "workspaces": {"studio": {
                          "team_id": "T000EXAMPLE", "identities": {"bot": {"token_env": "EXAMPLE_SLACK_TOKEN"}},
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
    core = Switchboard(config, path / "data", env={}, access_verifier=verify_access,
                       slack_options={"connect": False})
    core.projects.create(slug="operations", name="Operations")
    core.projects.create(slug="community", name="Community")
    discord = core.blocks["team-chat"]
    discord.guilds.add("1000")
    discord.guild_info["1000"] = {"name": "Engineering", "owner_id": "1001"}
    discord.channels["2001"] = Channel(None, "deployments", 0, "1000")
    discord.channels["2002"] = Channel(None, "community", 0, "1000")
    core.blocks["telegram"]._seen[-1001000] = {"id": -1001000, "title": None, "type": "supergroup"}
    core.modes.watch("team-chat", "engineering", "2001", "msg-example", "1001", "Alex",
                     "Release ready", None, None, project="operations")
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
