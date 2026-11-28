"""Deterministic synthetic seed for the console visual regression suite. Never real data.
SWB_SEED=busy (default), empty, or error (busy data, every /api call but the session fails with 500).
SWB_NOW is the frozen server clock in epoch seconds. Run from web/ with ../.venv/bin/python."""
import asyncio
import base64
import os
import random
import secrets
import shutil
from io import BytesIO
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
import uvicorn
import yaml
from channel_modes_fixture import HOST, SLACK_TEAM_ID, SlackFixture, verify_access
from PIL import Image, ImageDraw
from starlette.responses import JSONResponse

from switchboard import connect, memory
from switchboard.app import create_app
from switchboard.blocks.discord import Channel
from switchboard.core import Switchboard
from switchboard.modes import ChatDrop
from switchboard.slack import store as slack_store

PORT = int(os.environ.get("SWB_PLAYWRIGHT_PORT", "4190"))
MODE = os.environ.get("SWB_SEED", "busy")
NOW = float(os.environ.get("SWB_NOW", "1768478400"))
rng = random.Random(20261128)
seed_time = NOW - 15 * 86400


def clock() -> float:
    return seed_time


def tick(seconds: float = 60) -> None:
    global seed_time
    seed_time += seconds


class PublicFixtureIds:
    def __init__(self):
        self.ids = count(1)

    def token_hex(self, n: int) -> str:
        return next(self.ids).to_bytes(n, "big").hex()

    def token_urlsafe(self, n: int) -> str:
        return base64.urlsafe_b64encode(next(self.ids).to_bytes(n, "big")).decode().rstrip("=")

    choice = staticmethod(secrets.choice)


class PairingIds(PublicFixtureIds):
    token_urlsafe = staticmethod(secrets.token_urlsafe)
CAPTAINS = [
    ("jam_EXAMPLEATLAS", "Atlas", "#50cea2"), ("jam_EXAMPLECONTEXTOR", "Contextor", "#e0a040"),
    ("jam_EXAMPLEMERIDIAN", "Meridian of the Northern Release Train and Regional Failover", "#6b8ff0"),
    ("jam_EXAMPLEHARBOR", "Harbor", "#c8372d"), ("jam_EXAMPLEQUILL", "Quill", "#8a5cd0"),
    ("jam_EXAMPLEUNNAMED", "", "#777777"),
]
PROJECTS = [
    ("operations", "Operations"), ("community", "Community"), ("release-train", "Release train"),
    ("apac-edge-failover", "Infrastructure migration and regional failover for the Asia Pacific edge network"),
    ("billing", "Billing"), ("docs-site", "Docs site"), ("mobile", "Mobile"), ("research", "Research"),
    ("security-review", "Security review"), ("growth", "Growth"), ("design-system", "Design system"),
    ("data-pipeline", "Data pipeline"), ("support", "Support"), ("onboarding-v2", "Onboarding v2"),
]
AGENTS = ["claude-code", "codex", "chatgpt", "gemini-cli", "grok", "claude", "cursor", "muse",
          "northwind-procurement-assistant-staging"]
TITLES = ["Release checklist review", "Deploy canary to the edge", "Rotate the GitHub App key", "Triage issue 4127",
          "Weekly status digest", "Summarize the incident timeline for the regional failover drill on Tuesday night and list every follow up owner",
          "Draft the changelog", "Check flaky test in channel modes", "Reply to the community thread", "Migrate memory backup remote",
          None, "Review PR 312", "Plan the docs parity pass", "Investigate 502s on the tunnel", "Label new issues"]


def captain_png(color: str) -> bytes:
    image = Image.new("RGB", (256, 256), "#132b42")
    ImageDraw.Draw(image).ellipse((30, 30, 226, 226), fill=color)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


async def capy_fixture(request: httpx.Request) -> httpx.Response:
    if request.url.host == "swb.example.com":
        if request.url.path == "/api/v1/captains":
            items = [] if MODE == "empty" else CAPTAINS
            return httpx.Response(200, json={"items": [{"id": i, "name": n, "authorId": "user_EXAMPLE",
                "avatar": {"kind": "preset", "value": i, "image": f"https://swb.example.com/{i}.png"}} for i, n, _ in items]})
        if request.url.path == "/api/v1/users":
            return httpx.Response(200, json={"items": [{"id": "user_EXAMPLE", "firstName": "Alex", "lastName": "Rivera"}]})
        for i, _, color in CAPTAINS:
            if request.url.path == f"/{i}.png":
                return httpx.Response(200, content=captain_png(color))
    return httpx.Response(404)


SENDERS = [
    lambda c: {"kind": "agent", "agent": "claude-code", "name": "Claude Code"},
    lambda c: {"kind": "agent", "agent": "codex", "name": "Codex"},
    lambda c: {"kind": "agent", "agent": "chatgpt", "name": "ChatGPT"},
    lambda c: {"kind": "agent", "agent": "grok", "name": "Grok"},
    lambda c: {"kind": "agent", "agent": "gemini-cli", "name": "Gemini CLI"},
    lambda c: c.blocks["capy"]._from(rng.choice(CAPTAINS[:5])[0], {"source": "assistant"}),
    lambda c: {"kind": "slack", "workspace": "studio", "channel": "C0PLANNING", "channel_name": "planning", "user": "U0ALEX", "user_name": "Alex Rivera"},
    lambda c: {"kind": "discord", "channel": "2001", "channel_name": "deployments", "user": "1001", "user_name": "Morgan Okafor-Lindqvist"},
    lambda c: {"kind": "telegram", "channel": "-1001000", "user_name": "dispatch"},
    lambda c: {"kind": "github", "author": "octo-reviewer", "title": "Flaky test in channel modes"},
]


async def seed_inbox(core: Switchboard) -> None:
    for i in range(1, 341):
        tick(15)
        project = PROJECTS[i % len(PROJECTS)][0] if i % 9 else None
        owner = AGENTS[i % len(AGENTS)]
        dest, ref, target = ("capy", CAPTAINS[i % 5][0], f"capy:captain:{CAPTAINS[i % 5][0]}") if i % 4 == 0 else ("mailbox", f"ref-{i}", None)
        title = TITLES[i % len(TITLES)]
        handle = core.inbox.open_handle(handle=f"h_seed_{i:04d}", owner=owner, destination=dest, ref=ref, project=project,
                                        title=title, meta={}, target=target)
        sender = SENDERS[i % len(SENDERS)](core)
        await core.inbox.add(handle, "message", {"text": f"{title or 'Untitled'}. Message {i} with enough body text to wrap across a narrow column on a phone.", "from": sender})
        if i % 3 == 0:
            await core.inbox.add(handle, "reply", {"text": "Done. The change is ready to review.", "from": SENDERS[(i + 5) % len(SENDERS)](core)})
        if i % 5 == 0:
            core.inbox.update_handle(handle, status="completed", open=False)
        if i % 37 == 0:
            core.store.x("INSERT INTO conversation_turns(root, turns, paused_at) VALUES (?, ?, ?)", (handle, 200, core.clock()))
    for j in range(12):
        tick()
        pairing = core.connections.mint(agent=AGENTS[j % len(AGENTS)], project=PROJECTS[j % len(PROJECTS)][0], client=None, by="owner")
        key = core.connections.redeem(pairing["code"])
        with patch.object(core.slack.store, "clock", lambda: NOW - 1800 + j * 60):
            core.slack.store.create_request(workspace="studio", identity="bot", action="post_message", params={
                "channel": "planning" if j % 2 else "release-coordination-and-regional-failover",
                "text": f"Draft post {j}. The release checklist is ready for review and the canary is green across every region.",
            }, persona=None, key_id=key["key_id"], ttl_seconds=3600 * (j + 1))


def make_core(path: Path) -> Switchboard:
    busy = MODE != "empty"
    blocks = {"mcp": {"use": "mcp"}, "capy": {"use": "capy", "api_base": "https://swb.example.com/api/v1"}}
    env = {"CAPY_API_KEY": "example-capy-fixture", "EXAMPLE_SLACK_TOKEN": "example-slack-fixture",
           "GIT_COMMIT": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "BUILD_DATE": "2026-01-15"}
    if busy:
        env["SWITCHBOARD_KEY_ALPHA"] = "example-alpha-fixture-key"
        blocks.update({
            "team-chat": {"use": "discord", "connect": False, "spaces": {"engineering": {"guild_id": "1000", "project": "operations", "channels": {
                "2001": "assist", "2002": {"mode": "off", "project": "community"}, "2003": "watch", "2004": "triage"}}}},
            "slack": {"use": "slack", "public_base_url": "https://swb.example.com",
                      "personas": {"example": {"name": "Review bot", "avatar": "review-bot.png"}},
                      "workspaces": {"studio": {"team_id": SLACK_TEAM_ID, "identities": {"bot": {"token_env": "EXAMPLE_SLACK_TOKEN"}}}},
                      "spaces": {"studio": {"channels": {"#planning": "watch", "#release-coordination-and-regional-failover": "assist"}}}},
            "telegram": {"use": "telegram", "connect": False, "spaces": {"dispatch": {"chats": [-1001000], "mode": "watch"}}},
            "repo-events": {"use": "github", "spaces": {"release": {"repo": "example-org/example-repo", "project": "operations", "mode": "triage"},
                                                        "docs": {"repo": "example-org/a-much-longer-repository-name-for-the-docs-site", "project": "docs-site", "mode": "watch"}}},
            "routines": {"use": "claude-routine", "routines": {
                "release-captain": {"routine_id": "trig_EXAMPLE0001", "project": "release-train", "primary": True, "name": "Release captain"},
                "nightly-docs-parity-checker-for-every-locale": {"routine_id": "trig_EXAMPLE0002", "project": "docs-site"},
                "general-helper": {"routine_id": "trig_EXAMPLE0003"}}},
            "grok": {"use": "grok-routine", "routines": {"grok-triage": {"project": "support", "url_env": "GROK_URL_A", "secret_env": "GROK_SECRET_A"}}},
            "dots": {"use": "dots", "delivery": "mailbox", "dots": {"example-dot": {"mailbox": "chatgpt"}, "research-dot": {"mailbox": "chatgpt"}}},
        })
        env.update({"CLAUDE_ROUTINE_TOKEN_RELEASE_CAPTAIN": "x", "GROK_URL_A": "https://example.test/grok", "GROK_SECRET_A": "x"})
    config = path / "switchboard.yaml"
    config.write_text(yaml.safe_dump({"version": 2, "public_url": "http://localhost:4190", "timezone": "UTC",
        "auth": {"owner_emails": ["owner@example.test"], "cloudflare_access": {"team_domain": "access.example.test", "aud": "channel-mode-fixture"}},
        "blocks": blocks}))
    transport = httpx.MockTransport(capy_fixture)
    return Switchboard(config, path / "data", env=env, clock=clock, access_verifier=verify_access,
                       block_options={"capy": {"transport": transport}}, http_transport=transport,
                       slack_options={"connect": False, "client_factory": lambda _token: SlackFixture(), "clock": clock})


def seed_busy(core: Switchboard) -> None:
    for slug, name in PROJECTS:
        core.projects.create(slug=slug, name=name)
    core.store.x("INSERT INTO owner_passkeys(id, name, public_key, sign_count, created_at, last_used_at) VALUES (?,?,?,?,?,?)",
                 ("Zml4dHVyZQ", "Fixture passkey", b"fixture", 0, 1767268800, 1767355200))
    core.store.x("INSERT INTO owner_passkeys(id, name, public_key, sign_count, created_at, last_used_at) VALUES (?,?,?,?,?,?)",
                 ("Zml4dHVyZTI", "Studio Mac Touch ID with a very long passkey label", b"fixture2", 0, 1767268800, None))
    for n in range(60):
        tick()
        src = ["claude-code", "codex", "chatgpt", "grok", "admin:owner@example.test"][n % 5]
        scope = "general" if n % 3 == 0 else f"project:{PROJECTS[n % len(PROJECTS)][0]}" if n % 3 == 1 else f"agent:{['claude-code','codex'][n % 2]}"
        core.memory.write(source=src, scope=scope, namespace=["notes", "decisions", "runbooks"][n % 3], key=f"note-{n:03d}" + ("-with-a-very-long-key-name-that-will-not-fit" if n % 11 == 0 else ""),
                          title=TITLES[n % len(TITLES)] or "Untitled note", body=("Review the change and record the decision. " * (1 + n % 6)).strip(),
                          tags=["release"] if n % 4 == 0 else None, confirm_general=True, content_trust="owner" if src.startswith("admin") else "agent")
    discord = core.blocks["team-chat"]
    discord.guilds.add("1000")
    discord.guild_info["1000"] = {"name": "Engineering", "owner_id": "1001"}
    for cid, name in [("2001", "deployments"), ("2002", "community"), ("2003", "release-coordination-and-regional-failover-war-room"), ("2004", "bug-reports")]:
        discord.channels[cid] = Channel(None, name, 0, "1000")
    core.blocks["telegram"]._seen[-1001000] = {"id": -1001000, "title": None, "type": "supergroup"}
    core.modes.watch("team-chat", "engineering", "2001", "msg-example", "1001", "Alex", "Release ready", None, None, project="operations")
    outcomes = ["delivered", "dropped", "refused", "delivered", "delivered", "error"]
    actions = ["send", "reply", "webhook", "memory_write", "connect_redeem", "route_set", "post_message"]
    for n in range(220):
        tick(3)
        core.audit.record(action=actions[n % len(actions)], origin=["mcp", "repo-events", "slack", "team-chat"][n % 4],
                          outcome=outcomes[n % len(outcomes)], destination=["mailbox", "capy", "slack", "repo-events"][n % 4], source=AGENTS[n % len(AGENTS)],
                          client="local", target=["example-org/example-repo", "capy:captain:jam_EXAMPLEMERIDIAN", "#planning", None][n % 4],
                          title=TITLES[n % len(TITLES)], detail="channel mode off" if n % 6 == 1 else None)
    core.modes.chat_drop("team-chat", "engineering", "2002", ChatDrop.TRIAGE_TARGET)
    asyncio.run(seed_inbox(core))
    core.memory_git._save(
        "admin:owner@example.test", remote="git@example.test:memory-backup.git", enabled=False,
        last_attempt_at=NOW - 3600, last_success_at=NOW - 3600, last_commit="abc1234",
    )


def run(path: Path) -> None:
    global seed_time
    core = make_core(path)
    if MODE != "empty":
        ids = PublicFixtureIds()
        with (patch.object(connect, "secrets", PairingIds()),
              patch.object(slack_store, "secrets", ids),
              patch.object(memory, "new_id", lambda prefix: f"{prefix}_{int(clock() * 1000)}{ids.token_hex(4)}")):
            seed_busy(core)
    seed_time = NOW
    app = create_app(core)

    async def service(scope, receive, send):
        if MODE == "error" and scope["type"] == "http" and scope["path"].startswith("/api/"):
            return await JSONResponse({"error": "fixture_unavailable"}, status_code=500)(scope, receive, send)
        await app(scope, receive, send)
    uvicorn.run(service, host=HOST, port=PORT, access_log=False)


if __name__ == "__main__":
    if MODE not in {"busy", "empty", "error"}:
        raise SystemExit(f"Invalid SWB_SEED: {MODE}")
    if root := os.environ.get("SWB_VISUAL_DATA"):
        fixture_path = Path(root) / MODE
        fixture_path.mkdir(parents=True)
        try:
            run(fixture_path)
        finally:
            shutil.rmtree(fixture_path)
    else:
        with TemporaryDirectory(prefix=f"swb-visual-{MODE}-") as d:
            run(Path(d))
