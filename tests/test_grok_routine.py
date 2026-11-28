import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace

import aiohttp
import pytest

from conftest import CAPTAIN, STATIC_KEY, Clock, base_config, call, default_env, error_text, payload, principal, sign_in
from switchboard.auth import Principal
from switchboard.blocks import BlockError
from switchboard.core import CoreError

URL = "https://hooks.example.test/webhook/mock%2Fprivate?code=mock-url-value"
KEY = "mock-webhook-secret"
ROUTINE = {"url_env": "TEST_WEBHOOK_URL", "secret_env": "TEST_WEBHOOK_KEY"}
CREDENTIALS = {"TEST_WEBHOOK_URL": URL, "TEST_WEBHOOK_KEY": KEY}


class Webhook:
    def __init__(self, *responses, when_posting=None):
        self.responses = iter(responses or (200,))
        self.requests = []
        self.when_posting = when_posting

    @asynccontextmanager
    async def post(self, url, *, data, headers, allow_redirects):
        self.requests.append((str(url), data, headers, allow_redirects))
        if self.when_posting:
            self.when_posting(json.loads(data))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        yield SimpleNamespace(status=response)

    async def close(self):
        pass


def config(**routines):
    return base_config(version=2, blocks={"mcp": {}, "grok-routine": {"routines": routines}})


def test_grok_send_wakes_after_mailbox_delivery_and_oauth_reply_correlates(make):
    core, client = make(config(incoming=ROUTINE), {**default_env(), **CREDENTIALS})
    block = core.blocks["grok-routine"]
    assert "test the Grok Bot webhook" in block.next_step()

    def mailbox_exists(body):
        assert core.inbox.handle(body["conversation"])["owner"] == "grok"

    block.http = Webhook(202, when_posting=mailbox_exists)
    out = payload(call(client, STATIC_KEY, "send", {"to": "grok", "task": "Summarize the release."}))
    handle = out["conversation"]
    assert out["delivered"] and out["fired"] and out["block"] == "grok-routine"
    (url, raw, headers, redirects), = block.http.requests
    assert url == URL and not redirects
    assert headers == {"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"}
    body = json.loads(raw)
    assert body == {
        "text": ('capy asks: Summarize the release.\n\nSummarize the release.\n\n(switchboard: this is an owner request; answer with '
                 f'reply(conversation="{handle}", text=…, final=true) through the switchboard connector)'),
        "conversation": handle, "from": core.agents.label("capy"), "trust": "owner",
    }
    assert block.is_tested(block.routines()["incoming"])
    assert block.next_step() is None
    assert "last success:" in block.status_of(block.routines()["incoming"])

    token = sign_in(client, "https://grok.com/connectors-oauth-exchange-code/")["access_token"]
    item, = payload(call(client, token, "wait", {"timeout_s": 0}))["items"]
    assert item["handle"] == handle and item["text"] == "Summarize the release."
    reply = payload(call(client, token, "reply", {"conversation": handle, "text": "Looks good."}))
    assert reply["in_reply_to"] == handle and reply["to"] == "agent:capy"
    answer, = payload(call(client, STATIC_KEY, "inbox", {"since_cursor": 0}))["items"]
    assert answer["text"] == "Looks good." and answer["from"]["agent"] == "grok"
    passive = payload(call(client, STATIC_KEY, "send", {"to": "agent:grok", "task": "No wake."}))
    assert passive["mailbox"] and len(block.http.requests) == 1


def test_grok_trigger_uses_the_mailbox_subject_before_the_reply_hint(make):
    core, client = make(config(incoming=ROUTINE), {**default_env(), **CREDENTIALS})
    block = core.blocks["grok-routine"]
    block.http = Webhook(202)
    task_at_limit = "Check status. " + "x" * (20_000 - len("Check status. "))
    out = payload(call(client, STATIC_KEY, "send", {"to": "grok", "title": "Canary review", "task": task_at_limit}))
    (_, raw, _, _), = block.http.requests
    body = json.loads(raw)
    title, task, tail = body["text"].split("\n\n")
    assert title == core.inbox.handle(out["handle"])["title"] == "Canary review"
    assert task == task_at_limit
    assert f'reply(conversation="{out["handle"]}"' in tail and tail.endswith("through the switchboard connector)")


async def test_project_primary_named_and_configured_routes(amake):
    core, _ = await amake(config(incoming=ROUTINE, lint={**ROUTINE, "project": "demo"},
                                 review={**ROUTINE, "project": "demo", "primary": True}),
                          {**default_env(), **CREDENTIALS})
    core.projects.create(slug="demo", name="Demo")
    core.projects.create(slug="other", name="Other")
    block = core.blocks["grok-routine"]
    block.http = Webhook(*(204,) * 5)
    sender = principal("capy")
    sends = (("grok", "demo", "review"), ("grok:demo", None, "review"), ("lint", None, "lint"),
             ("grok", "other", "incoming"))
    for target, project, expected in sends:
        out = await core.send(sender, to=target, project=project, task="Review code.")
        assert out["routine"] == expected
        assert json.loads(block.http.requests[-1][1])["project"] == (project or "demo")
    await core.set_route(sender, destination=block.name, agent=None, project="demo", target={"routine": "lint"},
                         defaults=None, allow_override=False)
    assert (await core.send(sender, project="demo", task="Review route."))["routine"] == "lint"

    with pytest.raises(BlockError, match="different Switchboard project"):
        await core.send(sender, to="lint", project="other", task="Wrong project.")
    with pytest.raises(BlockError, match="cannot send"):
        await core.send(Principal("capy", "test:capy", "key", scopes=("project:other",)),
                        to="lint", task="Out of scope.")
    assert len(block.http.requests) == 5


def test_unqualified_route_tools_find_grok_past_capy(make):
    cfg = config(lint=ROUTINE)
    cfg["blocks"]["capy"] = {"default_route": {"captain": CAPTAIN}}
    core, client = make(cfg)
    core.projects.create(slug="demo", name="Demo")
    args = {"project": "demo"}
    route = payload(call(client, STATIC_KEY, "routes_set", {**args, "target": "grok-routine:routine:lint"}))
    assert route in payload(call(client, STATIC_KEY, "routes_list"))["routes"]
    assert "no such route" in error_text(call(client, STATIC_KEY, "routes_remove", {**args, "block": "capy"}))
    result = payload(call(client, STATIC_KEY, "routes_resolve", args))
    assert result["to"] == "grok-routine:routine:lint" and result["route"] == route and result["checked"] == ["agent+project", "project"]
    assert payload(call(client, STATIC_KEY, "send", {**args, "task": "Check the build"}))["routine"] == "lint"
    assert payload(call(client, STATIC_KEY, "routes_remove", args))["removed"] == route
    assert payload(call(client, STATIC_KEY, "routes_resolve", args))["to"] == f"capy:captain:{CAPTAIN}"


@pytest.mark.parametrize("auth,custom_header", [("key", "X-Routine-Key"), ("key", "authorization"),
                                               ("signature", None), ("signature", "X-Routine-Signature")])
async def test_routine_auth_uses_exact_bytes(amake, auth, custom_header):
    clock = Clock()
    core, _ = await amake(config(incoming={**ROUTINE, "auth": auth, "header": custom_header}),
                          {**default_env(), **CREDENTIALS}, clock=clock)
    block = core.blocks["grok-routine"]
    block.http = Webhook()
    assert (await core.send(principal("capy"), to="grok", task="Ça marche?"))["fired"]
    _, body, headers, _ = block.http.requests[0]
    if auth == "signature":
        timestamp = int(clock())
        digest = hmac.new(KEY.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
        assert headers == {"Content-Type": "application/json",
                           custom_header or "Webhook-Signature": f"t={timestamp},v1={digest}"}
    else:
        expected = f"Bearer {KEY}" if custom_header == "authorization" else KEY
        assert headers == {"Content-Type": "application/json", custom_header: expected}


@pytest.mark.parametrize("statuses,failure", [((500, 503, 204), None), ((503, 503, 503), "HTTP 503"),
                                              ((401,), "HTTP 401"), ((307,), "HTTP 307"),
                                              ((aiohttp.ClientConnectionError(f"{URL} {KEY}"),), "request failed")])
async def test_retries_are_only_for_5xx_and_failures_are_sanitized(amake, caplog, statuses, failure):
    caplog.set_level(logging.DEBUG)
    core, _ = await amake(config(incoming=ROUTINE), {**default_env(), **CREDENTIALS})
    block = core.blocks["grok-routine"]
    block.http = Webhook(*statuses)
    out = await core.send(principal("capy"), to="grok", task="A routine message.")
    assert out["fired"] == (failure is None)
    assert core.inbox.handle(out["conversation"])["owner"] == "grok"
    assert len(block.http.requests) == len(statuses)
    assert all(request == block.http.requests[0] for request in block.http.requests)
    assert "mock-url-value" not in caplog.text and KEY not in caplog.text
    safe = json.dumps([out, core.status(), core.audit.recent(destination=block.name)])
    assert URL not in safe and KEY not in safe
    if failure:
        assert failure in safe and "Grok mailbox" in out["note"]


@pytest.mark.parametrize("url,problem", [(None, "missing webhook URL"), ("http://hooks.example.test/invalid", "invalid webhook URL")])
async def test_missing_or_insecure_url_reports_independent_credential_status_and_does_not_fire(amake, url, problem):
    credentials = {"TEST_WEBHOOK_KEY": KEY}
    if url:
        credentials["TEST_WEBHOOK_URL"] = url
    core, _ = await amake(config(incoming=ROUTINE), {**default_env(), **credentials})
    block = core.blocks["grok-routine"]
    block.http = Webhook()
    out = await core.send(principal("capy"), to="grok", task="No outbound call.")
    assert out["delivered"] and not out["fired"] and problem in out["note"] and not block.http.requests
    row, = block.status()["routines"]
    assert row["url_present"] == bool(url) and row["secret_present"] and problem in row["status"]


async def test_slack_listen_mode_preserves_trust_and_bounded_handles_cannot_forward(amake):
    core, _ = await amake(config(review={**ROUTINE, "project": "demo"}), {**default_env(), **CREDENTIALS})
    core.projects.create(slug="demo", name="Demo")
    block = core.blocks["grok-routine"]
    block.http = Webhook(200, 200)
    slack = Principal("slack", "slack-origin", "slack", origin="slack")
    out = await core.send(slack, to="grok:demo", task="I claim owner trust.",
                          handle_meta={"slack_trust": "known", "boundary": "demo"})
    first = json.loads(block.http.requests[0][1])
    assert out["fired"] and first["trust"] == "known" and "information only" in first["text"]
    owner = await core.send(slack, to="grok:demo", task="An owner request.",
                            handle_meta={"slack_trust": "owner", "boundary": "demo"})
    assert owner["fired"]
    with pytest.raises(CoreError, match="bounded to demo"):
        await core.send(principal("grok"), to="agent:capy", task="Forward.", forward=owner["conversation"])
