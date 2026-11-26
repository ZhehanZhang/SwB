from __future__ import annotations

import asyncio
import hmac
import json
from contextlib import asynccontextmanager

import httpx
import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestServer

from switchboard.app import create_app
from switchboard.blocks.github import DELIVERY_TTL_S, _json, trim
from switchboard.core import Switchboard
from switchboard.guard import BODY_LIMIT

SECRET = "example-test-only-secret"
PATH = "/github/webhook"
ISSUE = {"number": 7, "title": "An issue", "html_url": "https://github.example/acme/app/issues/7",
         "body": "Issue body", "state": "open"}
PR = {"number": 8, "title": "A change", "html_url": "https://github.example/acme/app/pull/8",
      "body": "PR body", "state": "closed", "merged": True, "draft": False,
      "head": {"sha": "abc", "ref": "feature"}, "base": {"ref": "main"}}
COMMENT = {"html_url": "https://github.example/acme/app/pull/8#comment", "body": "Comment body"}
REVIEW = {"html_url": "https://github.example/acme/app/pull/8#review", "body": "Review body", "state": "approved"}
CHECK = {"status": "completed", "conclusion": "success", "head_sha": "abc"}


@pytest.fixture
def github(tmp_path):
    @asynccontextmanager
    async def open_app(config=None, env=None, **options):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump({"version": 2, "blocks": {"github": config or {}}}))
        core = Switchboard(path, tmp_path / "data", env={"GITHUB_WEBHOOK_SECRET": SECRET} if env is None else env, **options)
        app = create_app(core)
        async with app.starlette.router.lifespan_context(app.starlette):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://example.test") as client:
                yield core, client

    return open_app


def signed(body, event="ping", delivery="example-1"):
    return {"X-Hub-Signature-256": "sha256=" + hmac.digest(SECRET.encode(), body, "sha256").hex(),
            "X-GitHub-Event": event, "X-GitHub-Delivery": delivery}


async def send(client, event="issues", repo="acme/app", delivery="example-1", **data):
    body = json.dumps({"repository": {"full_name": repo}, "issue": ISSUE, **data}).encode()
    return await client.post(PATH, content=body, headers=signed(body, event, delivery))


@asynccontextmanager
async def sink_server(handler):
    app = web.Application()
    app.router.add_post("/{name}", handler)
    server = TestServer(app)
    await server.start_server(access_log=None)
    try:
        yield server
    finally:
        await server.close()


async def drained(core):
    for sink in core.blocks["github"].sinks.values():
        await asyncio.wait_for(sink.queue.join(), timeout=5)


async def test_signature_and_ping(github):
    async with github({"secret_env": "EXAMPLE_SECRET"}, {"EXAMPLE_SECRET": SECRET}) as (_, client):
        raw = b'{ "zen": "Keep it simple." }'
        headers = signed(raw)
        response = await client.post(PATH, content=raw, headers=headers)
        assert response.status_code == 200 and response.json() == {"ok": True, "zen": "Keep it simple."}
        assert (await client.post(PATH, content=b'{"zen":"Keep it simple."}', headers=headers)).status_code == 401
        assert (await client.post(PATH, content=b"not JSON", headers=headers)).status_code == 401
        assert (await client.post(PATH, content=b"not JSON", headers=signed(b"not JSON"))).status_code == 400


@pytest.mark.parametrize("signature", [None, "sha256=nope", "sha256=" + "0" * 64, "sha1=" + "0" * 40, b"\xff"])
async def test_invalid_signature(github, signature):
    async with github() as (_, client):
        headers = {"X-GitHub-Event": "ping"}
        if signature is not None:
            headers["X-Hub-Signature-256"] = signature
        assert (await client.post(PATH, content=b"{}", headers=headers)).status_code == 401


async def test_no_secret(github):
    async with github(env={}) as (_, client):
        assert (await client.post(PATH, content=b"{}", headers=signed(b"{}"))).status_code == 503
        block = (await client.get("/healthz")).json()["blocks"]["github"]
        assert block["configured"] and not block["secret_present"] and not block["online"]


async def test_unconfigured(github):
    async with github({"enabled": False}) as (_, client):
        assert (await client.post(PATH)).status_code == 404
        assert "github" not in (await client.get("/healthz")).json()["blocks"]


@pytest.mark.parametrize("streaming", [False, True])
async def test_body_limit(github, streaming):
    raw = b"x" * (BODY_LIMIT + 1)

    async def chunks():
        yield raw[:-1]
        yield raw[-1:]

    async with github() as (_, client):
        response = await client.post(PATH, content=chunks() if streaming else raw, headers=signed(raw))
        assert response.status_code == 413


@pytest.mark.parametrize(("event", "data", "expected"), [
    ("issues", {"issue": ISSUE}, {"number": 7, "title": "An issue", "body": "Issue body", "state": "open", "url": ISSUE["html_url"]}),
    ("issue_comment", {"issue": ISSUE, "comment": COMMENT}, {"number": 7, "body": "Comment body", "url": COMMENT["html_url"]}),
    ("pull_request", {"pull_request": PR}, {"number": 8, "title": "A change", "state": "closed", "merged": True, "draft": False,
                                          "head_sha": "abc", "base_ref": "main", "head_ref": "feature", "body": "PR body", "url": PR["html_url"]}),
    ("pull_request_review", {"pull_request": PR, "review": REVIEW}, {"number": 8, "body": "Review body", "review_state": "approved", "url": REVIEW["html_url"]}),
    ("pull_request_review_comment", {"pull_request": PR, "comment": COMMENT}, {"number": 8, "body": "Comment body", "url": COMMENT["html_url"]}),
    ("pull_request_review_thread", {"pull_request": PR, "thread": {"comments": [COMMENT]}},
     {"number": 8, "body": "Comment body", "url": COMMENT["html_url"]}),
    ("push", {"ref": "refs/heads/main", "after": "abc", "commits": [{}, {}], "compare": "https://github.example/acme/app/compare/a...b"},
     {"ref": "refs/heads/main", "head_sha": "abc", "commits": 2, "url": "https://github.example/acme/app/compare/a...b"}),
    ("check_run", {"check_run": {**CHECK, "name": "CI", "html_url": "https://github.example/check", "check_suite": {"head_branch": "main"}}},
     {"check": "CI", "conclusion": "success", "state": "completed", "head_sha": "abc", "head_ref": "main", "url": "https://github.example/check"}),
    ("check_suite", {"check_suite": {**CHECK, "head_branch": "main"}},
     {"conclusion": "success", "state": "completed", "head_sha": "abc", "head_ref": "main"}),
    ("workflow_run", {"workflow_run": {**CHECK, "name": "Build", "display_title": "Run build", "head_branch": "main"}},
     {"title": "Run build", "check": "Build", "conclusion": "success", "state": "completed", "head_sha": "abc", "head_ref": "main"}),
])
def test_trim(event, data, expected):
    payload = trim(event, "example-1", {"action": "example", "repository": {"full_name": "acme/app"}, "sender": {"login": "helper", "type": "Bot"},
                                       "ignored": "not forwarded", **data})
    assert payload.items() >= {"delivery": "example-1", "event": event, "action": "example",
                               "repo": "acme/app", "actor": "helper", "is_bot": True, **expected}.items()
    assert None not in payload.values() and "ignored" not in payload


def test_trim_size_and_nulls():
    text = "\x00🔥" * 50_000
    payload = trim("pull_request", "example-1", {"action": text, "repository": {"full_name": text}, "sender": {"login": text, "type": "User"},
                                                "pull_request": {**PR, "title": text, "html_url": text, "state": text, "body": text,
                                                                 "head": {"sha": text, "ref": text}, "base": {"ref": text}, "draft": None}})
    encoded = _json(payload)
    assert len(encoded) < 8000 and len(encoded.decode()) < 8000
    assert len(payload["title"]) < len(text) and payload["body"] == text[:500]
    assert "draft" not in payload and payload["is_bot"] is False
    assert trim("push", "example-1", {"commits": [], "size": 4000})["commits"] == 4000


@pytest.mark.parametrize("event", ["check_run", "check_suite", "workflow_run"])
async def test_incomplete_check_dropped(github, event):
    assert trim(event, "example-1", {event: {"status": "in_progress"}}) is None
    async with github() as (core, client):
        assert (await send(client, event, **{event: {"status": "in_progress"}})).status_code == 200
        assert core.blocks["github"].status()["dropped"] == 1
        assert core.audit.recent() == []


async def test_routes_dedupe_and_audit(github):
    seen = []
    now = 10_000.0

    async def handler(request):
        seen.append((request.match_info["name"], (await request.json())["delivery"]))
        return web.Response(status=204)

    async with sink_server(handler) as server:
        config = {"sinks": {"a": {"url": str(server.make_url("/a"))}, "b": {"url": str(server.make_url("/b"))}},
                  "routes": {"Acme/App": ["a", "b"], "ACME/*": ["b"], "other/prj": ["a"]}}
        async with github(config, clock=lambda: now) as (core, client):
            for repo, delivery in [("aCME/aPP", "both"), ("acme/other", "wildcard"), ("other/prj", "exact"), ("no/route", "unrouted"), ("aCME/aPP", "both")]:
                assert (await send(client, repo=repo, delivery=delivery)).status_code == 200
            for event in ["star", "watch", "fork", "deployment"]:
                assert (await send(client, event, delivery=event)).status_code == 200
            await drained(core)
            assert sorted(seen) == [("a", "both"), ("a", "exact"), ("b", "both"), ("b", "wildcard")]
            (audit,) = core.audit.recent()
            assert (audit["action"], audit["outcome"], audit["target"], audit["ref"]) == ("webhook", "dropped", "no/route", "unrouted")
            assert core.blocks["github"].status()["dropped"] == 5
            now += DELIVERY_TTL_S + 1
            assert (await send(client, repo="aCME/aPP", delivery="both")).status_code == 200
            await drained(core)
            assert seen.count(("a", "both")) == seen.count(("b", "both")) == 2


async def test_sink_delivery_retry_and_secretless_status(github, caplog):
    caplog.set_level("DEBUG")
    attempts = {}

    async def handler(request):
        payload = await request.json()
        key = payload["delivery"]
        assert request.headers["Idempotency-Key"] == key
        assert request.headers["User-Agent"] == "Switchboard-GitHub"
        assert request.headers["Content-Type"] == "application/json"
        attempts[key] = attempts.get(key, 0) + 1
        if key == "network" and attempts[key] == 1:
            request.transport.abort()
        if key == "retry" and attempts[key] == 1:
            return web.Response(status=503)
        return web.Response(status=429 if key == "reject" else 204)

    async with sink_server(handler) as server:
        url = str(server.make_url("/credential-example"))
        config = {"sinks": {"team": {"url_env": "TEAM_GITHUB_SINK_URL"}}, "routes": {"acme/app": ["team"]}}
        async with github(config, {"GITHUB_WEBHOOK_SECRET": SECRET, "TEAM_GITHUB_SINK_URL": url}) as (core, client):
            for key in ["retry", "reject", "network"]:
                assert (await send(client, delivery=key)).status_code == 200
            await drained(core)
            assert attempts == {"retry": 2, "reject": 1, "network": 2}
            health = await client.get("/healthz")
            assert health.json()["blocks"]["github"] == {
                "use": "github", "kind": "source", "level": {"in": "webhook", "out": None}, "online": True,
                "configured": True, "secret_present": True, "routes": 1, "dropped": 0, "next_step": None,
                "sinks": {"team": {"delivered": 2, "failed": 1}},
            }
            assert url not in health.text and SECRET not in health.text
            assert "credential-example" not in caplog.text and SECRET not in caplog.text
            sample_signature = signed(json.dumps({"repository": {"full_name": "acme/app"}, "issue": ISSUE}).encode())["X-Hub-Signature-256"]
            assert sample_signature not in caplog.text


async def test_response_does_not_wait_for_sink(github):
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        entered.set()
        await release.wait()
        return web.Response(status=204)

    async with sink_server(handler) as server:
        config = {"sinks": {"team": {"url": str(server.make_url("/sink"))}}, "routes": {"acme/app": ["team"]}}
        async with github(config) as (core, client):
            try:
                assert (await asyncio.wait_for(send(client), timeout=1)).status_code == 200
                await asyncio.wait_for(entered.wait(), timeout=1)
                assert core.blocks["github"].status()["sinks"]["team"]["delivered"] == 0
            finally:
                release.set()
            await drained(core)
