"""Claude routines: a send to a routine starts a Claude Code session, and its reply comes back tied to the conversation."""

import json

import httpx
from starlette.testclient import TestClient

from conftest import PUBLIC, STATIC_KEY, CIMDHost, Clock, access_verifier, call, default_env, error_text, payload, sign_in
from switchboard.app import create_app
from switchboard.config import translate_v1
from switchboard.core import Switchboard
from conftest import base_config

ROUTINE = "trig_01ROUTINE"


class Anthropic:
    def __init__(self, status=200):
        self.status = status
        self.requests = []

    def handler(self, request):
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "nope"})
        return httpx.Response(200, json={"type": "routine_fire", "claude_code_session_id": "session_01X",
                                         "claude_code_session_url": "https://claude.ai/code/session_01X"})


def build(tmp_path, capy, anthropic, env_extra=None, name="r"):
    cfg = translate_v1(base_config())
    cfg["blocks"]["claude-routine"] = {"use": "claude-routine", "routines": {"claude": {"routine_id": ROUTINE, "token_env": "ROUTINE_TOKEN"}}}
    env = {**default_env(), **(env_extra if env_extra is not None else {"ROUTINE_TOKEN": "sk-ant-oat01-test"})}
    path = tmp_path / f"{name}.yaml"
    import yaml

    path.write_text(yaml.safe_dump(cfg))
    core = Switchboard(path, tmp_path / f"data-{name}", env=env, clock=Clock(),
                       oauth_http=httpx.AsyncClient(transport=httpx.MockTransport(CIMDHost().handler)), access_verifier=access_verifier,
                       destination_options={"capy": {"transport": httpx.MockTransport(capy.handler)},
                                            "claude-routine": {"transport": httpx.MockTransport(anthropic.handler)}})
    client = TestClient(create_app(core), base_url=PUBLIC, follow_redirects=False)
    client.__enter__()
    return core, client


def test_send_to_claude_fires_the_routine_and_the_reply_correlates(tmp_path, capy):
    anthropic = Anthropic()
    core, c = build(tmp_path, capy, anthropic)
    try:
        out = payload(call(c, STATIC_KEY, "send", {"task": "please summarise the deploy log", "to": "claude"}))
        assert out["fired"] and out["session_url"] == "https://claude.ai/code/session_01X" and out["block"] == "claude-routine"
        (req,) = anthropic.requests
        assert str(req.url) == f"https://api.anthropic.com/v1/claude_code/routines/{ROUTINE}/fire"
        assert req.headers["authorization"] == "Bearer sk-ant-oat01-test" and req.headers["anthropic-version"]
        text = json.loads(req.content)["text"]
        handle = out["conversation"]
        assert text.startswith("please summarise the deploy log") and f'reply(conversation="{handle}"' in text
        assert core.inbox.handle(handle)["state"]["routine"]["session_id"] == "session_01X"
        tok = sign_in(c)["access_token"]
        (item,) = payload(call(c, tok, "wait", {"timeout_s": 0}))["items"]
        assert item["handle"] == handle and item["text"] == "please summarise the deploy log"
        back = payload(call(c, tok, "reply", {"conversation": handle, "text": "deploy looks clean"}))
        assert back["in_reply_to"] == handle and back["to"] == "agent:capy"
        (got,) = payload(call(c, STATIC_KEY, "inbox", {"since_cursor": 0}))["items"]
        assert got["text"] == "deploy looks clean" and got["in_reply_to"] == handle and got["from"]["agent"] == "claude"
        routines = [t for t in payload(call(c, STATIC_KEY, "targets"))["targets"] if t["block"] == "claude-routine"]
        assert [(t["kind"], t["id"], t["address"]) for t in routines] == [("routine", "claude", "claude-routine:routine:claude")]
        assert payload(call(c, STATIC_KEY, "send", {"task": "just a note", "to": "agent:claude"}))["mailbox"] and len(anthropic.requests) == 1
    finally:
        c.__exit__(None, None, None)


def test_without_a_token_or_on_failure_the_message_stays_in_the_mailbox(tmp_path, capy):
    for anthropic, env, name in ((Anthropic(), {}, "a"), (Anthropic(status=401), None, "b")):
        core, c = build(tmp_path, capy, anthropic, env, name)
        try:
            out = payload(call(c, STATIC_KEY, "send", {"task": "hello", "to": "claude"}))
            assert out["delivered"] and not out["fired"] and "mailbox" in out["note"]
            tok = sign_in(c)["access_token"]
            assert [i["text"] for i in payload(call(c, tok, "wait", {"timeout_s": 0}))["items"]] == ["hello"]
            if env == {}:
                assert any("ROUTINE_TOKEN" in p for p in core.blocks["claude-routine"].problems()) and not anthropic.requests
        finally:
            c.__exit__(None, None, None)


def test_connected_agents_cannot_wake_a_routine(tmp_path, capy):
    anthropic = Anthropic()
    core, c = build(tmp_path, capy, anthropic)
    try:
        code = payload(call(c, STATIC_KEY, "connect_code", {"agent": "codex"}))["code"]
        key = c.post("/connect", json={"code": code}).json()["key"]
        assert "only from the owner" in error_text(call(c, key, "send", {"task": "run rm -rf", "to": "claude"}))
        assert not anthropic.requests
    finally:
        c.__exit__(None, None, None)
