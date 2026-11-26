import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from conftest import OWNER, PUBLIC, STATIC_KEY, base_config, call, error_text, payload, sign_in
from switchboard.auth import Principal

WEBHOOK_URL = "https://grok.example.test/private-trigger"
WEBHOOK_KEY = "mock-grok-webhook-key"
CHOICE = {"name": "grok-demo", "project": "demo", "auth": "key", "header": "X-Routine-Key", "primary": True}


@pytest.fixture
def grok(make):
    core, client = make(base_config(version=2, blocks={"mcp": {}, "grok-routine": {}}))
    core.projects.create(slug="demo", name="Demo")
    return core, client


def connect(client, **kwargs):
    response = call(client, STATIC_KEY, "connect", {"what": "grok", **kwargs})
    assert not response.get("isError"), response
    return payload(response)


def enter_claim(client, step, value):
    assert step["by"] == "person" and step["link"].startswith(f"{PUBLIC}/claim/")
    path = urlsplit(step["link"]).path
    assert path.rsplit("/", 1)[-1] not in step["do"]
    assert client.get(path).status_code == 403
    page = client.get(path, headers=OWNER)
    assert page.status_code == 200 and 'type="password"' in page.text
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(path, data={"csrf": csrf, "token": value}, headers=OWNER)
    assert value not in response.text
    return response


def test_grok_discovery_returns_connector_prompt_and_registration(grok):
    core, client = grok
    result = connect(client, project="demo")
    assert "state" not in result
    assert result["done"] == [f"MCP URL: {PUBLIC}/mcp.", "OAuth sign-in: available."]
    connector, routine, register = result["next"]
    assert connector["by"] == "person"
    assert all(text in connector["do"] for text in (f"{PUBLIC}/mcp", "New Connector > Custom", "sign in with OAuth",
                                                      'connect(what="mcp-client"', "pairing code"))
    assert routine["by"] == "person"
    assert all(text in routine["do"] for text in ("custom webhook trigger", "text", "conversation", "project", "from", "trust",
                                                    "Act only when trust is owner", "reply(conversation=", "final=true"))
    assert register["by"] == "you" and register["call"] == {
        "tool": "connect", "arguments": {"what": "grok", "choose": {
            "name": "<routine name>", "project": "demo", "auth": "key",
        }},
    }
    assert not core.blocks["grok-routine"].routines()
    assert not core.store.q("SELECT 1 FROM secrets")
    assert not core.store.q("SELECT 1 FROM connect_codes")


def test_grok_connect_claims_resumes_and_waits_for_successful_test(grok, monkeypatch):
    core, client = grok
    block = core.blocks["grok-routine"]
    posted = []
    replies = iter((401, 200, 401))

    class HTTP:
        @asynccontextmanager
        async def post(self, url, *, headers, data, allow_redirects):
            assert not allow_redirects
            posted.append((str(url), json.loads(data), headers))
            yield SimpleNamespace(status=next(replies))

        async def close(self):
            pass

    monkeypatch.setattr(block, "http", HTTP())
    first = connect(client, choose=CHOICE)
    assert "state" not in first
    assert block.routines()["grok-demo"]["primary"]
    assert block.routines()["grok-demo"]["header"] == "X-Routine-Key"
    url, key = first["next"]
    assert "Webhook POST URL" in url["do"] and "webhook key" in key["do"]
    resumed = connect(client, project="demo")
    assert len(block.routines()) == 1
    assert all(client.get(step["link"], headers=OWNER).status_code == 410 for step in first["next"])
    url, key = resumed["next"]
    assert enter_claim(client, url, WEBHOOK_URL.replace("https://", "http://")).status_code == 400
    assert enter_claim(client, url, WEBHOOK_URL).status_code == 200
    partial = connect(client, choose={"name": "grok-demo"})
    assert "state" not in partial
    remaining, = partial["next"]
    assert "webhook key" in remaining["do"] and remaining["link"] != key["link"]
    assert enter_claim(client, remaining, WEBHOOK_KEY).status_code == 200
    assert not posted

    ready = connect(client, choose=CHOICE)
    assert "state" not in ready
    step, = ready["next"]
    action = step["call"]
    assert step["by"] == "you" and action == {"tool": "send", "arguments": {
        "to": "grok-routine:routine:grok-demo", "task": "switchboard setup test: reply pong",
    }}
    for status in (401, 200, 401):
        sent = payload(call(client, STATIC_KEY, action["tool"], action["arguments"]))
        assert sent["fired"] is (status == 200)
        after = connect(client, choose=CHOICE)
        assert (after.get("state") == "connected") is (status == 200)
        assert bool(after["next"]) is (status != 200)
        assert all(value not in json.dumps(after) for value in (WEBHOOK_URL, WEBHOOK_KEY))
    assert len(posted) == 3
    assert all(url == WEBHOOK_URL and body["project"] == "demo" and headers["X-Routine-Key"] == WEBHOOK_KEY
               for url, body, headers in posted)
    assert all(value.encode() not in row["sealed"] for row in core.store.q("SELECT sealed FROM secrets")
               for value in (WEBHOOK_URL, WEBHOOK_KEY))


def test_grok_discovery_resumes_after_oauth_and_preserves_partial_choices(grok):
    _, client = grok
    tokens = sign_in(client, redirect="https://grok.com/connectors/oauth/callback")
    result = connect(client, choose={"name": "grok-demo", "auth": "signature", "primary": True})
    assert "Grok is signed in." in result["done"]
    routine, register = result["next"]
    assert "webhook trigger" in routine["do"]
    assert register["call"]["arguments"]["choose"] == {
        "name": "grok-demo", "project": "<Switchboard project slug>", "auth": "signature", "primary": True,
    }
    assert all(tokens[key] not in json.dumps(result) for key in ("access_token", "refresh_token"))


@pytest.mark.parametrize("restriction", [{"scopes": ("project:demo",)}, {"tools": ("connect",)},
                                         {"targets": ("*",)}, {"kind": "connect"}, {"trust": "known"}])
def test_grok_limited_callers_cannot_register_or_mint_claims(grok, monkeypatch, restriction):
    core, client = grok
    block = core.blocks["grok-routine"]
    principal = replace(Principal("limited", "key:limited", "key"), **restriction)
    monkeypatch.setattr(core.auth, "verify_bearer", lambda _token: principal)
    for result in (connect(client, project="demo", choose=CHOICE),
                   client.portal.call(block.connect_steps, "grok", principal, "demo", CHOICE)):
        assert not result["done"] and "state" not in result
        step, = result["next"]
        assert set(step) == {"by", "do"} and step["by"] == "person"
        assert "grok-demo" not in step["do"]
    assert not block.routines()
    assert not core.store.q("SELECT 1 FROM secrets")


def test_grok_resume_selects_among_routines_without_minting_claims(grok):
    core, client = grok
    connect(client, choose=CHOICE)
    connect(client, choose={**CHOICE, "name": "grok-review", "auth": "signature", "primary": False})
    before = [dict(r) for r in core.store.q("SELECT name, code_hash FROM secrets")]
    result = connect(client, project="demo")
    choice, *options = result["next"]
    assert choice["by"] == "person" and "Choose" in choice["do"]
    assert {s["call"]["arguments"]["choose"]["name"] for s in options} == {"grok-demo", "grok-review"}
    assert [dict(r) for r in core.store.q("SELECT name, code_hash FROM secrets")] == before
    selected = connect(client, choose={"name": "grok-review"})
    assert len(selected["next"]) == 2 and "signing secret" in selected["next"][1]["do"]


def test_grok_connect_rejects_credentials_in_choose_without_echoing(grok):
    core, client = grok
    response = call(client, STATIC_KEY, "connect", {"what": "grok", "choose": {**CHOICE, "url": WEBHOOK_URL}})
    message = error_text(response)
    assert "Never pass webhook credentials" in message and WEBHOOK_URL not in message
    assert not core.blocks["grok-routine"].routines()
    assert not core.store.q("SELECT 1 FROM secrets")


def test_grok_resume_rejects_different_routine_auth_without_rotating_claims(grok):
    core, client = grok
    initial = connect(client, choose=CHOICE)
    response = call(client, STATIC_KEY, "connect", {"what": "grok", "choose": {**CHOICE, "auth": "signature"}})
    assert "different authentication settings" in error_text(response)
    assert core.blocks["grok-routine"].routines()["grok-demo"]["auth"] == "key"
    assert all(client.get(step["link"], headers=OWNER).status_code == 200 for step in initial["next"])
