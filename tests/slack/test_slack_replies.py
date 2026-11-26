"""Replies to conversations that started in Slack, including private-target replies with no owner message."""

import asyncio
import json

import httpx
import pytest
import yaml
from starlette.testclient import TestClient

from conftest import call, error_text
from slackfix import BOT_USER, ENV, KEY_FULL, OWNER_A, FakeWorld, Hooks, base_config
from switchboard.app import create_app
from switchboard.core import Switchboard

PUBLIC = "https://switchboard.example.test"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


async def access(assertion):
    return None


def fired(request):
    return httpx.Response(200, json={"type": "routine_fire", "claude_code_session_id": "session_S", "claude_code_session_url": "https://claude.ai/code/session_S"})


def build(tmp_path, resources=None, *, workspace_project=None, channels=None):
    world, hooks, routine = FakeWorld(), Hooks(), []
    slack = base_config()
    slack["workspaces"]["a2a"].pop("sinks", None)
    slack.update(workspace_projects={"a2a": workspace_project}, channel_projects={"a2a": channels or {}})
    routines = {"claude": {"routine_id": "trig_01X", "token_env": "ROUTINE_TOKEN", "agent": "full"}}
    for slug in ("workspace", "channel", "explicit"):
        routines[f"claude-{slug}"] = {**routines["claude"], "routine_id": f"trig_{slug}", "project": slug}
    cfg = {"public_url": PUBLIC, "slack": slack,
           "destinations": {"claude-routine": {"routines": routines}},
           "routes": [{"match": {"project": "explicit"}, "to": "claude-routine:routine:claude-explicit"}],
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
    for slug in ("workspace", "channel", "explicit", "no-routine"):
        core.projects.create(slug=slug, name=slug.title())
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


@pytest.mark.parametrize("resources", [{}, {"target:claude": "private"}])
def test_a_routine_answers_a_slack_message_with_reply(tmp_path, resources):
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
        assert post["channel"] == "CPUBLIC01" and post["thread_ts"] == "1700000500.000001" and post["text"] == "up 3 days"


def test_reply_on_a_slack_event_conversation_posts_in_its_thread(tmp_path):
    core, world, _ = build(tmp_path, {"target:full": "private"})
    with TestClient(create_app(core), base_url=PUBLIC, follow_redirects=False) as client:
        client.portal.call(core.origins["slack"].refresh)
        client.portal.call(core.slack.ingest, "a2a", mention("full anyone around?", ts="1700000600.000001"))
        items = [i for i in tool(client, "wait", {"timeout_s": 0})["items"] if i["kind"] == "slack_event"]
        out = tool(client, "reply", {"conversation": items[0]["handle"], "text": "here"})
        assert out["status"] == "sent" and out["posted_in"] == "CPUBLIC01" and out["thread_ts"] == "1700000600.000001"
        (post,) = posts(world, client)
        assert post["channel"] == "CPUBLIC01" and post["thread_ts"] == "1700000600.000001" and post["text"] == "here" and post["username"] == "Capy"


@pytest.mark.parametrize("channels, slug, trigger, project, boundary", [
    ({}, "claude", "workspace", "workspace", "workspace"),
    ({"chan-cpublic01": "channel"}, "claude", "channel", "channel", "channel"),
    ({"CPUBLIC01": "channel", "chan-cpublic01": None}, "claude", "channel", "channel", "channel"),
    ({"CPUBLIC01": None}, "claude", "01X", None, None),
    ({"CPUBLIC01": ""}, "claude", "01X", None, None),
    ({"CPUBLIC01": "no-routine"}, "claude", "01X", "no-routine", "no-routine"),
    ({}, "claude-explicit", "explicit", "explicit", "workspace"),
    ({}, "explicit", "explicit", "explicit", "workspace"),
])
def test_slack_routine_place_mapping(tmp_path, channels, slug, trigger, project, boundary):
    core, _, routine = build(tmp_path, workspace_project="workspace", channels=channels)
    with TestClient(create_app(core), base_url=PUBLIC, follow_redirects=False) as client:
        client.portal.call(core.origins["slack"].refresh)
        client.portal.call(core.slack.ingest, "a2a", mention(f"{slug} what is the uptime?"))
        (req,) = routine
        assert req.url.path.endswith(f"/trig_{trigger}/fire")
        (item,) = [i for i in tool(client, "inbox", {"since_cursor": 0})["items"] if i["kind"] == "message"]
        handle = core.inbox.handle(item["handle"])
        assert handle["project"] == project and handle["meta"].get("boundary") == boundary
        if boundary:
            out = call(client, KEY_FULL, "send", {"task": "Hand this off", "to": "agent:claude", "forward": handle["handle"]})
            assert f"bounded to {boundary}" in error_text(out)


def test_slack_forwarded_final_reaches_original_thread(tmp_path):
    core, world, routine = build(tmp_path)
    with TestClient(create_app(core), base_url=PUBLIC, follow_redirects=False) as client:
        client.portal.call(core.origins["slack"].refresh)
        client.portal.call(core.slack.ingest, "a2a", mention("claude what is the uptime?"))
        (item,) = [i for i in tool(client, "inbox", {"since_cursor": 0})["items"] if i["kind"] == "message"]
        original = item["handle"]
        forwarded = tool(client, "send", {"task": "Check uptime", "to": "claude:explicit", "forward": original})["handle"]
        assert len(routine) == 2 and routine[-1].url.path.endswith("/trig_explicit/fire")
        assert forwarded in json.loads(routine[-1].content)["text"]
        tool(client, "reply", {"conversation": forwarded, "text": "Still checking", "final": False})
        assert core.inbox.read(owner="slack", since=0)["items"] == []
        tool(client, "reply", {"conversation": forwarded, "text": "Up 3 days"})
        (post,) = posts(world, client)
        assert post["channel"] == "CPUBLIC01" and post["thread_ts"] == "1700000500.000001" and post["text"] == "Up 3 days"
        (answer,) = core.inbox.read(owner="slack", since=0)["items"]
        assert answer["in_reply_to"] == original and answer["final"]
        assert core.inbox.handle(original)["status"] == "answered"


def test_forward_cannot_retry_after_routine_message_reaches_mailbox(tmp_path, monkeypatch):
    core, _, _ = build(tmp_path)
    with TestClient(create_app(core), base_url=PUBLIC, follow_redirects=False) as client:
        client.portal.call(core.origins["slack"].refresh)
        client.portal.call(core.slack.ingest, "a2a", mention("claude what is the uptime?"))
        (item,) = [i for i in tool(client, "inbox", {"since_cursor": 0})["items"] if i["kind"] == "message"]
        original = item["handle"]
        args = {"task": "Check uptime", "to": "claude:explicit", "forward": original}

        async def fail(*args, **kwargs):
            raise RuntimeError("wake failed")

        with monkeypatch.context() as patch:
            patch.setattr(core.blocks["claude-routine"].http, "post", fail)
            assert "wake failed" in error_text(call(client, KEY_FULL, "send", args))
        (delivered,) = [i for i in core.inbox.read(owner="full", since=0)["items"] if i.get("text") == "Check uptime"]
        retry = call(client, KEY_FULL, "send", args)
        assert retry.get("isError") and "has already been forwarded" in error_text(retry)
        assert core.inbox.handle(original)["meta"]["forwarded_to"] == delivered["handle"]


def test_owner_message_to_mapped_private_routine_stays_in_thread(tmp_path):
    core, world, _ = build(tmp_path, {"target:claude": "shared", "target:claude-workspace": "private"},
                           workspace_project="workspace")
    with TestClient(create_app(core), base_url=PUBLIC, follow_redirects=False) as client:
        client.portal.call(core.origins["slack"].refresh)
        client.portal.call(core.slack.ingest, "a2a", mention("claude what is the uptime?"))
        (item,) = [i for i in tool(client, "inbox", {"since_cursor": 0})["items"] if i["kind"] == "message"]
        tool(client, "reply", {"conversation": item["handle"], "text": "Up 3 days"})
        (post,) = posts(world, client)
        assert post["channel"] == "CPUBLIC01" and post["thread_ts"] == "1700000500.000001" and post["text"] == "Up 3 days"
        (audit,) = [r for r in core.slack.store.recent_audit() if r["action"] == "routed"]
        assert audit["detail"]["target"] == "claude-workspace"


def test_private_routine_without_owner_message_replies_in_owner_dm(tmp_path):
    core, world, _ = build(tmp_path)
    with TestClient(create_app(core), base_url=PUBLIC, follow_redirects=False) as client:
        client.portal.call(core.origins["slack"].refresh)
        client.portal.call(core.slack.ingest, "a2a", {**mention("claude what is the uptime?"), "user": "USTRANGER"})
        (item,) = [i for i in tool(client, "inbox", {"since_cursor": 0})["items"] if i["kind"] == "message"]
        assert item["from"]["trust"] == "external"
        core.visibility.set("target:claude", "private", core.clock(), "test")
        tool(client, "reply", {"conversation": item["handle"], "text": "Up 3 days"})
        (post,) = posts(world, client)
        assert post["channel"] == "DAPPROVER" and post.get("thread_ts") is None
        assert post["text"] == "Reply from claude (Claude routine) to a message in a shared channel:\n\nUp 3 days"


def test_shared_non_owner_slack_message_does_not_upgrade_or_forward(tmp_path):
    core, _, routine = build(tmp_path, workspace_project="workspace")
    with TestClient(create_app(core), base_url=PUBLIC, follow_redirects=False) as client:
        client.portal.call(core.origins["slack"].refresh)
        client.portal.call(core.slack.ingest, "a2a", {**mention("claude hello"), "user": "USTRANGER"})
        (req,) = routine
        assert req.url.path.endswith("/trig_01X/fire")
        (item,) = [i for i in tool(client, "inbox", {"since_cursor": 0})["items"] if i["kind"] == "message"]
        assert item["from"]["trust"] == "external"
        out = call(client, KEY_FULL, "send", {"task": "Hand this off", "to": "agent:claude", "forward": item["handle"]})
        assert "can only forward an owner-trust conversation" in error_text(out)

        core.store.x("UPDATE handles SET meta = json_remove(json_set(meta, '$.from.trust', 'owner'), '$.slack_trust') "
                     "WHERE handle = ?", (item["handle"],))
        out = call(client, KEY_FULL, "send", {"task": "Hand this off", "to": "agent:claude", "forward": item["handle"]})
        assert "can only forward an owner-trust conversation" in error_text(out)
