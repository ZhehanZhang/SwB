from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
import yaml

from switchboard.app import create_app
from switchboard.blocks.discord import Channel
from switchboard.core import Switchboard

HOST = "127.0.0.1"
PORT = 4178


async def verify_access(assertion: str) -> str | None:
    return "owner@example.test" if assertion == "channel-mode-fixture" else None


def run(path: Path) -> None:
    config = path / "switchboard.yaml"
    config.write_text(yaml.safe_dump({
        "version": 2,
        "public_url": f"http://{HOST}:{PORT}",
        "auth": {"owner_emails": ["owner@example.test"], "cloudflare_access": {
            "team_domain": "access.example.test", "aud": "channel-mode-fixture",
        }},
        "blocks": {
            "mcp": {"use": "mcp"},
            "team-chat": {"use": "discord", "connect": False, "spaces": {"engineering": {
                "guild_id": "1000", "project": "operations", "channels": {
                    "2001": "assist", "2002": {"mode": "off", "project": "community"},
                },
            }}},
            "slack": {"use": "slack", "workspaces": {"studio": {
                "team_id": "T000EXAMPLE", "identities": {"bot": {"token_env": "EXAMPLE_SLACK_TOKEN"}},
                "channels": {"#planning": "watch"},
            }}},
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
    core.modes.watch("team-chat", "engineering", "2001", "msg-example", "1001", "Alex",
                     "Release ready", None, None, project="operations")
    uvicorn.run(create_app(core), host=HOST, port=PORT)


if __name__ == "__main__":
    with TemporaryDirectory(prefix="swb-channel-modes-") as directory:
        run(Path(directory))
