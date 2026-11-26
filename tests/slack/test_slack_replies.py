"""Replies to conversations that started in Slack: a routine, an inbox watcher or any agent answers with reply, and the
answer lands in the Slack thread (or the owner's DM for a private target in a shared channel)."""

import asyncio
import json

import httpx
import pytest
import yaml
from starlette.testclient import TestClient

from slackfix import BOT_USER, ENV, KEY_FULL, OWNER_A, FakeWorld, Hooks, base_config
from switchboard.app import create_app
from switchboard.core import Switchboard

PUBLIC = "https://switchboard.example.test"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


async def access(assertion):
    return None


def fired(request):
    return httpx.Response(200, json={"type": "routine_fire", "claude_code_session_id": "session_S", "claude_code_session_url": "https://claude.ai/code/session_S"})


def build(tmp_path, resources=None):
    world, hooks, routine = FakeWorld(), Hooks(), []
    slack = base_config()
    slack["workspaces"]["a2a"].pop("sinks", None)
    cfg = {"public_url": PUBLIC, "slack": slack,
           "destinations": {"claude-routine": {"routines": {"claude": {"routine_id": "trig_01X", "token_env": "ROUTINE_TOKEN", "agent": "full"}}}},
           "visibility": {"defaults": {"target": "shared", "channel": "shared"}, "resources": resources or {}}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))

    def handler(request):
        routine.append(request)
        return fired(request)

    env = {**ENV, "SWITCHBOARD_KEY_FULL": KEY_FULL, "ROUTINE_TOKEN": "sk-ant-oat01-x"}
    core = Switchboard(path, tmp_path / "data", env=env, access_verifier=access,
                       slack_options={"client_factory": world.factory, "http": httpx.AsyncClient(transport=httpx.MockTransport(hooks.handler)),
                                      "own_post_grace": 0, "connect": False},
                       destination_options={"claude-routine": {"transport": httpx.MockTransport(handler)}})
    return core, world, routine


def tool(client, name, args):
    r = client.post("/mcp", headers={**HEADERS, "Authorization": f"Bearer {KEY_FULL}"},
                    content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}))
    res = r.json()["result"]
    assert not res.get("isError"), res
    return json.loads(res["content"][0]["text"])


def mention(text, ts="1700000500.000001"):
    return {"type": "app_mention", "user": OWNER_A, "channel": "CPUBLIC01", "channel_type": "channel", "ts": ts, "text": f"<@{BOT_USER}> {text}"}


def posts(world, client, n=1):
    for _ in range(100):
        got = [kw for t, n_, kw in world.calls if n_ == "chat_postMessage"]
        if len(got) >= n:
            return got
        client.portal.call(asyncio.sleep, 0.03)
    return [kw for t, n_, kw in world.calls if n_ == "chat_postMessage"]


@pytest.mark.parametrize("resources, channel, thread", [({}, "CPUBLIC01", "1700000500.000001"), ({"target:claude": "private"}, "DAPPROVER", None)])
def test_a_routine_answers_a_slack_message_with_reply(tmp_path, resources, channel, thread):
    core, world, routine = build(tmp_path, resources)
    with TestClient(create_app(core), base_url=PUBLIC, follow_redirects=False) as client:
        origin = core.origins["slack"]
        client.portal.call(origin.refresh)
        assert core.slack.targets["claude"].kind == "routine"
        client.portal.call(core.slack.ingest, "a2a", mention("claude what is the uptime?"))
        (req,) = routine
        note = json.loads(req.content)["text"]
        handle = note.split('reply(conversation="')[1].split('"')[0]
        assert "what is the uptime?" in note and "Don't use another handle" in note
        items = tool(client, "wait", {"timeout_s": 0})["items"]
        assert handle in {i["handle"] for i in items}
        out = tool(client, "reply", {"conversation": handle, "text": "up 3 days"})
        assert out["in_reply_to"] == handle and out["to"] == "agent:slack"
        (post,) = posts(world, client)
        assert post["channel"] == channel and post.get("thread_ts") == thread and post["text"].endswith("up 3 days")


def test_reply_on_a_slack_event_conversation_posts_in_its_thread(tmp_path):
    core, world, routine = build(tmp_path)
    with TestClient(create_app(core), base_url=PUBLIC, follow_redirects=False) as client:
        client.portal.call(core.slack.ingest, "a2a", mention("anyone around?", ts="1700000600.000001"))
        items = [i for i in tool(client, "wait", {"timeout_s": 0})["items"] if i["kind"] == "slack_event"]
        out = tool(client, "reply", {"conversation": items[0]["handle"], "text": "here"})
        assert out["status"] == "sent" and out["posted_in"] == "CPUBLIC01" and out["thread_ts"] == "1700000600.000001"
        (post,) = posts(world, client)
        assert post["channel"] == "CPUBLIC01" and post["thread_ts"] == "1700000600.000001" and post["text"] == "here" and post["username"] == "Capy"
