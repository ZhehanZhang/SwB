import asyncio
import hmac
import json
from types import SimpleNamespace

import httpx
import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestServer

from switchboard.claims import Claims
from switchboard.core import Switchboard
from switchboard.signing import stripe_signature
from switchboard.slack import push
from switchboard.store import Store
from switchboard.trust import CLOSE, OPEN

NOW = 1_700_000_000
SECRET = "stripe-signing-test-key"
SECRET_ENV = "TEST_SINK_SIGNING_SECRET"


@pytest.mark.parametrize("secret", [SECRET, SECRET.encode()])
def test_stripe_signature_known_vector(secret):
    assert stripe_signature(secret, '{"message":"café"}'.encode(), NOW) == (
        "t=1700000000,v1=8fcf5b5a038e928b8236622a36d38b3581fa9c8f687f49d2572fc2204db64055"
    )


def configure_sink(tmp_path, block, url, header, credential, **kwargs):
    sink = {"url": url, "signature": "stripe", "secret_env": SECRET_ENV}
    if header is not None:
        sink["header"] = header
    if block == "slack":
        config = {"workspaces": {"example": {"team_id": "T123", "intake": "none",
                                             "identities": {"bot": {"token_env": "TEST_BOT_TOKEN"}}, "sinks": [sink]}}}
    else:
        config = {"sinks": {"example": sink}, "routes": {"acme/app": ["example"]}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"version": 2, "blocks": {block: config}}))
    data = tmp_path / "data"
    env = {SECRET_ENV: SECRET} if credential == "env" else {}
    if credential == "claimed":
        data.mkdir()
        store = Store(data / "switchboard.db")
        try:
            claims = Claims(store)
            link = claims.mint(SECRET_ENV, "https://example.test")
            claims.consume(link.rsplit("/", 1)[-1], SECRET)
        finally:
            store.close()
    return Switchboard(path, data, env=env, clock=lambda: NOW + 0.75, **kwargs)


@pytest.mark.parametrize(("header", "credential"), [(None, "env"), ("X-Webhook-Signature", "claimed"), (None, None)])
async def test_slack_stripe_sink_signs_delivered_bytes(tmp_path, monkeypatch, caplog, header, credential):
    monkeypatch.setattr(push, "time", SimpleNamespace(time=lambda: NOW + 0.75))
    seen = []
    transport = httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(204))
    http = httpx.AsyncClient(transport=transport)
    core = configure_sink(tmp_path, "slack", "https://example.test/slack-private-url", header, credential,
                          slack_options={"http": http, "connect": False})
    caplog.set_level("DEBUG")
    try:
        await core.start()
        sink, = core.slack.sinks["example"]
        assert (await sink.deliver({"message": "café"}, idempotency_key="event")) == bool(credential)
        if not credential:
            assert not seen and sink.failed == 1
            return
        request, = seen
        expected = "t=1700000000,v1=928d02ef0a0a9b46caaef102d8f12c6eaf2e83fd9899dd78679ff134782dca10"
        assert request.content == b'{"message":"caf\\u00e9"}'
        assert request.headers[header or "Stripe-Signature"] == expected
        assert ("Stripe-Signature" in request.headers) == (header is None)
        assert "X-Switchboard-Timestamp" not in request.headers and "X-Switchboard-Signature" not in request.headers
        assert request.headers["Idempotency-Key"] == "slackgate:example:event"
        assert SECRET not in caplog.text and expected not in caplog.text and "slack-private-url" not in caplog.text
    finally:
        await core.aclose()


@pytest.mark.parametrize(("header", "credential"), [(None, "env"), ("X-Webhook-Signature", "claimed"), (None, None)])
async def test_github_stripe_sink_signs_delivered_bytes(tmp_path, caplog, header, credential):
    seen = []

    async def receive(request):
        seen.append((await request.read(), request.headers))
        return web.Response(status=204)

    app = web.Application()
    app.router.add_post("/github", receive)
    server = TestServer(app)
    await server.start_server(access_log=None)
    core = configure_sink(tmp_path, "github", str(server.make_url("/github")), header, credential)
    caplog.set_level("DEBUG")
    try:
        await core.start()
        github = core.blocks["github"]
        sink = github.sinks["example"]
        await github.dispatch("issues", "stripe-delivery", {
            "repository": {"full_name": "acme/app"},
            "issue": {"number": 7, "title": "café", "html_url": "https://github.example/acme/app/issues/7",
                      "body": "Issue body", "state": "open"},
        }, "127.0.0.1")
        await asyncio.wait_for(sink.queue.join(), timeout=5)
        if not credential:
            assert not seen and sink.failed == 1
            return
        (body, headers), = seen
        digest = hmac.digest(SECRET.encode(), f"{NOW}.".encode() + body, "sha256").hex()
        expected = f"t={NOW},v1={digest}"
        data = json.loads(body)
        assert data["title"] == "café" and b"caf\xc3\xa9" in body
        assert data["subject"] == "Unknown sender (external) on GitHub acme/app: Issue #7"
        assert data["trust"] == "external" and f"{OPEN}\nIssue body\n{CLOSE}" in data["body"]
        assert data["body"] in data["text"] and data["text"].endswith("GitHub delivery stripe-delivery.")
        assert headers[header or "Stripe-Signature"] == expected
        assert ("Stripe-Signature" in headers) == (header is None)
        assert headers["Idempotency-Key"] == "stripe-delivery"
        assert SECRET not in caplog.text and expected not in caplog.text
    finally:
        await core.aclose()
        await server.close()


async def test_slack_stripe_sink_does_not_follow_redirects(tmp_path, caplog):
    seen = []

    async def redirect(request):
        seen.append(request.path)
        return web.Response(status=307, headers={"Location": "/redirected",
                                                "X-Echo-Signature": request.headers["Stripe-Signature"],
                                                "X-Echo-Authorization": request.headers["Authorization"]})

    app = web.Application()
    app.router.add_post("/{path}", redirect)
    server = TestServer(app)
    await server.start_server(access_log=None)
    http = httpx.AsyncClient(follow_redirects=True, trust_env=False, headers={"Authorization": "Bearer example-test-credential"})
    core = configure_sink(tmp_path, "slack", str(server.make_url("/slack-private-url")), None, "env",
                          slack_options={"http": http, "connect": False})
    caplog.set_level("DEBUG")
    try:
        await core.start()
        (sink,) = core.slack.sinks["example"]
        assert not await sink.deliver({"message": "redirect"})
        assert seen == ["/slack-private-url"]
        assert SECRET not in caplog.text and "slack-private-url" not in caplog.text
        assert "v1=" not in caplog.text and "example-test-credential" not in caplog.text
    finally:
        await core.aclose()
        await server.close()
