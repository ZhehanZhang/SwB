from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from switchboard.slack.config import parse_config
from switchboard.slack.engine import SlackEngine as Core

ROOT = Path(__file__).resolve().parent.parent.parent
PAYLOADS = Path(__file__).resolve().parent / "payloads"

KEY_FULL = "k" * 32
KEY_FIXED = "f" * 32

ENV = {
    "A_BOT": "xoxb-a",
    "A_USER": "xoxp-a",
    "A_APP": "xapp-a",
    "B_USER": "xoxp-b",
    "B_APP": "xapp-b",
    "KEY_FULL": KEY_FULL,
    "KEY_FIXED": KEY_FIXED,
    "SINK_A": "https://hooks.example/a",
    "SINK_B": "https://hooks.example/b",
    "SINK_AUTH": "Bearer sink-secret",
}

TEAM_A, TEAM_B = "TAAAA1111", "TBBBB2222"
BOT_USER, BOT_ID, OWNER_A, OWNER_B = "UBOT00001", "BBOT00001", "UOWNERA01", "UOWNERB01"

AUTH = {
    "xoxb-a": {"ok": True, "team_id": TEAM_A, "user_id": BOT_USER, "bot_id": BOT_ID, "url": "https://workspace-a.example.test/"},
    "xoxp-a": {"ok": True, "team_id": TEAM_A, "user_id": OWNER_A, "url": "https://workspace-a.example.test/"},
    "xoxp-b": {"ok": True, "team_id": TEAM_B, "user_id": OWNER_B, "url": "https://workspace-b.example.test/"},
}


def base_config() -> dict[str, Any]:
    return {
        "personas": {
            "gate": {"name": "Gate", "emoji": ":robot_face:"},
            "capy": {"name": "Capy", "icon_url": "https://img.example/capy.png"},
            "other": {"name": "Other", "emoji": ":ghost:"},
        },
        "workspaces": {
            "a2a": {
                "team_id": TEAM_A,
                "app_token_env": "A_APP",
                "identities": {"bot": {"token_env": "A_BOT", "write": "free"}, "user": {"token_env": "A_USER", "write": "approval"}},
                "default_persona": "gate",
                "watched": ["CWATCH001"],
                "sinks": [{"name": "a", "url_env": "SINK_A", "headers_env": {"Authorization": "SINK_AUTH"}}],
            },
            "private": {
                "team_id": TEAM_B,
                "user_only": True,
                "app_token_env": "B_APP",
                "identities": {"user": {"token_env": "B_USER", "write": "approval"}},
                "watched": ["CPRIV0001"],
                "sinks": [{"name": "b", "url_env": "SINK_B"}],
            },
        },
        "approvals": {"ttl_minutes": 10, "notify_workspace": "a2a", "approver_user_id": OWNER_A},
        "visibility": {"defaults": {"target": "shared", "channel": "shared"}},
        "agents": {
            "full": {"persona": "capy", "allow_persona_override": True, "allowed_personas": ["capy", "gate"], "workspaces": {"a2a": ["bot", "user"], "private": ["user"]}},
            "fixed": {"persona": "capy", "allow_persona_override": False, "workspaces": {"a2a": ["bot"]}},
        },
    }


class FakeSlack:
    """Records every Web API call; answers from canned responses."""

    def __init__(self, token: str, world: "FakeWorld"):
        self.token = token
        self.world = world
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        async def call(**kwargs: Any) -> Any:
            self.calls.append((name, kwargs))
            self.world.calls.append((self.token, name, kwargs))
            if name in self.world.canned:
                return self.world.canned[name](self.token, kwargs)
            if name == "auth_test":
                return AUTH[self.token]
            if name == "chat_postMessage":
                self.world.counter += 1
                return {"ok": True, "channel": kwargs["channel"], "ts": f"1700000000.{self.world.counter:06d}"}
            if name == "conversations_open":
                return {"ok": True, "channel": {"id": "DAPPROVER"}}
            if name == "conversations_info":
                return {"ok": True, "channel": {"id": kwargs["channel"], "name": "chan-" + kwargs["channel"].lower()}}
            if name == "users_info":
                return {"ok": True, "user": {"id": kwargs["user"], "name": "name-" + kwargs["user"].lower(), "profile": {}}}
            if name == "conversations_list":
                return {"ok": True, "channels": [{"id": "CWATCH001", "name": "agents"}, {"id": "CPRIV0001", "name": "family"}]}
            if name == "conversations_history":
                return {"ok": True, "messages": self.world.history.get(kwargs["channel"], [])}
            return {"ok": True}

        return call


class FakeWorld:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.clients: dict[str, FakeSlack] = {}
        self.counter = 0
        self.history: dict[str, list[dict]] = {}
        self.canned: dict[str, Any] = {}

    def factory(self, token: str) -> FakeSlack:
        self.clients[token] = FakeSlack(token, self)
        return self.clients[token]

    def names(self, token: str | None = None) -> list[str]:
        return [n for t, n, _ in self.calls if token is None or t == token]


class Hooks:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status)

    def bodies(self, host_path: str | None = None) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if host_path is None or str(r.url).endswith(host_path)]


class Clock:
    def __init__(self, t: float = 1_700_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def world() -> FakeWorld:
    return FakeWorld()


@pytest.fixture
def hooks() -> Hooks:
    return Hooks()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make_core(tmp_path, world, hooks, clock):
    cores: list[Core] = []

    async def build(config: dict | None = None, env: dict | None = None) -> Core:
        env = ENV if env is None else env
        core = Core(
            parse_config(base_config() if config is None else config, env),
            tmp_path / "data",
            env=env,
            client_factory=world.factory,
            http=httpx.AsyncClient(transport=httpx.MockTransport(hooks.handler)),
            clock=clock,
            own_post_grace=0,
        )
        await core.start(connect=False)
        cores.append(core)
        return core

    yield build


async def drain(core: Core) -> None:
    import asyncio

    for _ in range(50):
        await asyncio.sleep(0.01)
        if all(s.queue.empty() for sinks in core.sinks.values() for s in sinks):
            await asyncio.sleep(0.02)
            return


def payload(name: str) -> dict:
    return json.loads((PAYLOADS / name).read_text())
