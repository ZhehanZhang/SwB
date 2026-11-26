from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest

from switchboard.auth import sha
from switchboard.blocks.mcp.events import EVENT

from conftest import Clock, SECOND_KEY, STATIC_KEY, arpc, base_config, default_env, eventually, principal

URL = "https://receiver.example.test:8443/hooks?token=callback-test-token"
PUBLIC_IP = "8.8.8.8"
KEY, NEXT_KEY = b"a" * 32, b"b" * 32


def params(mailbox="second", url=URL, key=KEY, **options):
    return {"name": EVENT, "arguments": {"mailbox_id": mailbox}, "delivery": {
        "mode": "webhook", "url": url, "secret": "whsec_" + base64.b64encode(key).decode(),
    }, "cursor": None, **options}


def pending(core):
    return core.store.q("SELECT * FROM mcp_event_deliveries ORDER BY next_at")


def subscription(core):
    return core.store.one("SELECT * FROM mcp_event_subscriptions")


class Receiver:
    def __init__(self):
        self.calls = []
        self.addresses = [PUBLIC_IP]
        self.lookups = []
        self.keys = [KEY]
        self.answers = []
        self.verify = None

    async def resolve(self, host, port):
        self.lookups.append((host, port))
        return self.addresses

    def post(self, request):
        item = json.loads(request.content)
        self.calls.append((request, item))
        assert request.method == "POST"
        assert request.headers["content-type"] == "application/json"
        assert request.headers["x-mcp-subscription-id"].startswith("sub_")
        if request.url.host == PUBLIC_IP:
            assert request.extensions["sni_hostname"] == "receiver.example.test"
            assert request.headers["Host"] == "receiver.example.test:8443"
        message = f"{request.headers['webhook-id']}.{request.headers['webhook-timestamp']}.".encode() + request.content
        expected = ["v1," + base64.b64encode(hmac.new(k, message, hashlib.sha256).digest()).decode() for k in self.keys]
        assert request.headers["webhook-signature"].split() == expected
        if item.get("type") == "verification":
            assert request.headers["webhook-id"].startswith("msg_verification_")
            assert len(base64.urlsafe_b64decode(item["challenge"] + "=")) == 32
            if self.verify is not None:
                return self.verify(request, item)
            return httpx.Response(200, json={"challenge": item["challenge"]})
        assert request.headers["webhook-id"] == item["eventId"]
        answer = self.answers.pop(0) if self.answers else 204
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer)

    def bind(self, core):
        events = core.blocks["mcp"].events
        events.resolver = self.resolve
        events.transport = httpx.MockTransport(self.post)
        return events


async def rpc(client, method, values=None, token=SECOND_KEY, modern=False):
    response = await arpc(client, token, method, values, modern=modern)
    assert response.status_code == 200, response.text
    return response.json()


async def subscribe(client, values=None, token=SECOND_KEY, modern=False):
    return await rpc(client, "events/subscribe", values or params(), token, modern)


async def send(core, mailbox="second", sender="capy", **options):
    return await core.agent_send(principal(sender), to=mailbox, text="private inbox body", **options)


@pytest.mark.parametrize("modern", [False, True])
async def test_advertises_and_lists_events(amake, modern):
    _, client = await amake()
    method = "server/discover" if modern else "initialize"
    init = None if modern else {"protocolVersion": "2025-03-26", "capabilities": {},
                                "clientInfo": {"name": "example", "version": "1"}}
    caps = (await rpc(client, method, init, modern=modern))["result"]["capabilities"]
    assert caps["events"] == {}
    if modern:
        assert caps["extensions"]["io.modelcontextprotocol/events"] == {}
    listed = (await rpc(client, "events/list", modern=modern))["result"]
    assert listed["nextCursor"] is None
    [event] = listed["events"]
    assert event["name"] == EVENT
    assert event["description"] == "A new message arrived in a switchboard mailbox."
    assert event["delivery"] == ["webhook"]
    assert event["inputSchema"] == {"type": "object", "properties": {"mailbox_id": {"type": "string"}},
                                    "required": ["mailbox_id"], "additionalProperties": False}
    assert event["payloadSchema"]["required"] == ["mailbox_id", "message_id", "conversation", "kind"]
    assert not (await rpc(client, "events/list", {"cursor": "end"}, modern=modern))["result"]["events"]


async def test_subscribe_delivery_get_message_and_reply(amake, caplog):
    caplog.set_level(logging.DEBUG)
    clock = Clock()
    core, client = await amake(clock=clock)
    receiver = Receiver()
    receiver.bind(core)
    response = (await subscribe(client, modern=True))["result"]
    sid = "sub_" + hashlib.sha256(json.dumps(
        ["second", "key:second", URL, EVENT, {"mailbox_id": "second"}], separators=(",", ":"), sort_keys=True,
    ).encode()).hexdigest()[:32]
    assert response["id"] == sid
    assert datetime.fromisoformat(response["refreshBefore"]).timestamp() == clock.t + 86400
    assert response["cursor"] is None and response["truncated"] is False
    assert subscription(core)["secret"] != KEY
    assert (core.data_dir / "mcp-events.key").stat().st_mode & 0o777 == 0o600

    await send(core, mailbox="gemini")
    await send(core, sender="second", allow_self=True)
    assert pending(core) == []
    sent = await send(core)
    await eventually(lambda: len(receiver.calls) == 2 and not pending(core))
    request, event = receiver.calls[1]
    [inbox_row] = core.store.q("SELECT * FROM inbox WHERE handle = ?", (sent["handle"],))
    assert event["eventId"] == "swb_evt_" + hashlib.sha256(f"{sid}{inbox_row['id']}".encode()).hexdigest()[:24]
    event_data = {"mailbox_id": "second", "message_id": f"msg_{inbox_row['id']}", "conversation": sent["conversation"], "kind": "message"}
    assert event == {"eventId": event["eventId"], "name": EVENT,
                     "timestamp": datetime.fromtimestamp(clock.t, timezone.utc).isoformat().replace("+00:00", "Z"),
                     "data": event_data, "cursor": None}
    assert "private inbox body" not in request.content.decode()
    assert request.headers["X-MCP-Subscription-Id"] == sid
    message = {"name": "get_message", "arguments": {"message_id": event_data["message_id"]}}
    result = (await rpc(client, "tools/call", message, modern=True))["result"]["structuredContent"]
    assert result["text"] == "private inbox body"
    assert result["from"]["agent"] == "capy" and result["from"]["trust"] == "owner"
    reply = await core.verb(principal("second"), "reply", sent["conversation"], text="reply")
    assert reply["in_reply_to"] == sent["conversation"]
    audit = str(core.audit.recent(limit=100))
    for protected in (URL, "callback-test-token", params()["delivery"]["secret"], request.headers["webhook-signature"]):
        assert protected not in caplog.text
        assert protected not in audit


async def test_refresh_rotates_secrets_and_caches_verification(amake):
    clock = Clock()
    core, client = await amake(clock=clock)
    receiver = Receiver()
    receiver.bind(core)
    first = (await subscribe(client, params(ttlMs=10**50)))["result"]
    assert datetime.fromisoformat(first["refreshBefore"]).timestamp() - clock.t == 7 * 86400
    clock.t += 60
    second = (await subscribe(client, params(key=NEXT_KEY)))["result"]
    assert second["id"] == first["id"] and len(receiver.calls) == 1
    assert datetime.fromisoformat(second["refreshBefore"]).timestamp() - clock.t == 86400
    receiver.keys = [NEXT_KEY, KEY]
    await send(core)
    await eventually(lambda: len(receiver.calls) == 2 and not pending(core))

    clock.t += 3599
    refreshed = (await subscribe(client, params(key=NEXT_KEY, ttlMs=None)))["result"]
    assert refreshed["id"] == first["id"] and len(receiver.calls) == 2
    assert refreshed["deliveryStatus"]["lastError"] is None
    assert refreshed["deliveryStatus"]["active"]
    clock.t += 2
    receiver.keys = [NEXT_KEY]
    await send(core)
    await eventually(lambda: len(receiver.calls) == 3 and not pending(core))
    clock.t += 86400
    assert (await subscribe(client, params(key=NEXT_KEY)))["result"]["id"] == first["id"]
    assert len(receiver.calls) == 4 and receiver.calls[-1][1]["type"] == "verification"


async def test_visibility_scope_and_get_message_ownership(amake, monkeypatch):
    scoped_key = "r" * 32
    core, client = await amake(base_config(auth={"key_scopes": {"restricted": ["project:example"]}}),
                               env={**default_env(), "SWITCHBOARD_KEY_RESTRICTED": scoped_key})
    receiver = Receiver()
    receiver.bind(core)
    original = core.auth.verify_bearer

    def verify(token):
        p = original(token)
        if p and p.source == "second":
            return replace(p, trust="known")
        if token == "other-principal":
            return replace(original(STATIC_KEY), kind="slack")
        return p

    monkeypatch.setattr(core.auth, "verify_bearer", verify)
    assert (await rpc(client, "events/list"))["result"]["events"]
    for values, token in ((params("not-an-agent"), SECOND_KEY), (params("capy"), SECOND_KEY),
                          (params("restricted"), scoped_key), (params("capy"), "other-principal")):
        assert (await subscribe(client, values, token))["error"]["code"] == -32024
    assert not receiver.calls and not receiver.lookups and subscription(core) is None
    assert not (await rpc(client, "events/list", token="other-principal"))["result"]["events"]
    assert not (await rpc(client, "events/list", token=scoped_key))["result"]["events"]

    out = await send(core, mailbox="capy", sender="gemini")
    message_id = core.store.one("SELECT id FROM inbox WHERE handle = ?", (out["handle"],))["id"]
    args = {"name": "get_message", "arguments": {"message_id": f"msg_{message_id}"}}
    assert (await rpc(client, "tools/call", args))["result"]["isError"]
    assert (await rpc(client, "tools/call", args, token=STATIC_KEY))["result"]["structuredContent"]["owner"] == "capy"
    core.visibility.set("target:capy", "shared", "config", core.clock())
    assert (await rpc(client, "tools/call", args))["result"]["structuredContent"]["owner"] == "capy"
    assert (await subscribe(client, params("capy")))["result"]["id"]
    await send(core, mailbox="capy", sender="gemini")
    await eventually(lambda: len(receiver.calls) == 2 and not pending(core))
    assert receiver.calls[-1][1]["data"]["mailbox_id"] == "capy"
    core.visibility.set("target:capy", "private", "config", core.clock())
    await send(core, mailbox="capy", sender="gemini")
    await eventually(lambda: subscription(core)["active"] == 0)
    assert not pending(core) and len(receiver.calls) == 2
    assert (await rpc(client, "tools/call", args))["result"]["isError"]


@pytest.mark.parametrize("url, addresses, reason", [
    ("http://receiver.example.test/", [PUBLIC_IP], None),
    ("https://name:pass@receiver.example.test/", [PUBLIC_IP], None),
    ("https://10.0.0.5/", [], "blocked_destination"),
    ("https://127.0.0.1/", [], "blocked_destination"),
    ("https://[fe80::1]/", [], "blocked_destination"),
    ("https://169.254.169.254/", [], "blocked_destination"),
    ("https://100.64.0.1/", [], "blocked_destination"),
    ("https://[::ffff:127.0.0.1]/", [], "blocked_destination"),
    ("https://[2002:7f00:1::]/", [], "blocked_destination"),
    ("https://[2001:0:7f00:1:0:0:f7f7:f7f7]/", [], "blocked_destination"),
    (URL, ["172.16.0.1"], "blocked_destination"),
    (URL, [PUBLIC_IP, "192.168.1.1"], "blocked_destination"),
    (URL, [], "unreachable"),
])
async def test_refuses_unsafe_callbacks(amake, url, addresses, reason):
    core, client = await amake()
    receiver = Receiver()
    receiver.addresses = addresses
    receiver.bind(core)
    error = (await subscribe(client, params(url=url)))["error"]
    if reason:
        assert error["code"] == -32015 and error["data"] == {"reason": reason}
    else:
        assert error["code"] == -32602
    assert not receiver.calls and subscription(core) is None


@pytest.mark.parametrize("status, response, expected", [
    (200, {"challenge": "wrong"}, {"reason": "challenge_failed"}),
    (200, {"challenge": "é"}, {"reason": "challenge_failed"}),
    (200, {"challenge": "a" * 65536}, {"reason": "challenge_failed"}),
    (302, {}, {"reason": "redirect"}),
    (403, {}, {"reason": "http_error", "status": 403}),
    (503, {}, {"reason": "http_error", "status": 503}),
])
async def test_callback_challenge_errors(amake, status, response, expected):
    core, client = await amake()
    receiver = Receiver()
    receiver.verify = lambda request, item: httpx.Response(status, json=response, headers={"Location": "https://other.example.test/"})
    receiver.bind(core)
    error = (await subscribe(client))["error"]
    assert error["code"] == -32015 and error["data"] == expected
    assert len(receiver.calls) == 1 and not subscription(core)
    assert not core.store.q("SELECT 1 FROM mcp_event_verifications")


@pytest.mark.parametrize("status", [204, 503])
async def test_retry_keeps_event_bytes_across_restart_and_resigns(amake, status):
    clock = Clock()
    core, client = await amake(clock=clock)
    receiver = Receiver()
    receiver.answers = [503, 429, httpx.ReadTimeout("private failure"), 408, status]
    receiver.bind(core)
    await subscribe(client)
    await send(core)
    await eventually(lambda: len(receiver.calls) == 2 and pending(core)[0]["attempts"] == 1)
    await core._test_close()
    core, _ = await amake(clock=clock)
    event_worker = receiver.bind(core)
    start = clock.t
    for attempt in range(1, 5):
        clock.t = pending(core)[0]["next_at"]
        event_worker.wake.set()
        await eventually(lambda: len(receiver.calls) == attempt + 2 and (
            not pending(core) if attempt == 4 else pending(core)[0]["attempts"] == attempt + 1))
    assert 800 <= clock.t - start <= 900
    requests = [req for req, _ in receiver.calls[1:]]
    assert len({r.content for r in requests}) == 1
    assert len({r.headers["webhook-id"] for r in requests}) == 1
    assert len({r.headers["webhook-timestamp"] for r in requests}) == 5
    assert len({r.headers["webhook-signature"] for r in requests}) == 5
    assert subscription(core)["consecutive_failures"] == (0 if status == 204 else 5)
    assert subscription(core)["last_status"] == str(status)


@pytest.mark.parametrize("status, active", [(410, False), (413, True), (400, True)])
async def test_terminal_delivery_response(amake, status, active):
    core, client = await amake()
    receiver = Receiver()
    receiver.answers = [status]
    receiver.bind(core)
    await subscribe(client)
    await send(core)
    await eventually(lambda: len(receiver.calls) == 2 and not pending(core))
    assert subscription(core)["last_status"] == str(status)
    assert bool(subscription(core)["active"]) == active
    refreshed = (await subscribe(client))["result"]["deliveryStatus"]
    assert refreshed["active"] == active and refreshed["lastError"] == "http_4xx"
    assert len(receiver.calls) == 2 and subscription(core)["active"] == 1


@pytest.mark.parametrize("change", ["expiry", "replace_key", "connect", "oauth", "remove_oauth_client", "dns"])
async def test_pending_delivery_stops_on_expiry_revocation_or_rebinding(amake, change):
    clock = Clock()
    core, client = await amake(clock=clock)
    receiver = Receiver()
    receiver.answers = [503]
    worker = receiver.bind(core)
    token = SECOND_KEY
    key_id = None
    if change == "connect":
        code = core.connections.mint(agent="second", project=None, client=None, by="config")
        pairing = core.connections.redeem(code["code"])
        token, key_id = pairing["key"], pairing["key_id"]
    if change in ("oauth", "remove_oauth_client"):
        core.store.x("INSERT INTO oauth_clients (client_id, redirect_uris, auth_method, kind, created_at) VALUES (?,?,?,?,?)",
                     ("test-client", "[]", "none", "dcr", clock.t))
        grant = core.auth._issue("test-client", core.cfg.resource, "second")
        token = grant["access_token"]
        if change == "oauth":
            family = core.store.one("SELECT family FROM oauth_tokens WHERE token_hash = ?", (sha(token),))["family"]
            rotated = core.auth._issue("test-client", core.cfg.resource, "second", family)
            assert core.auth.verify_bearer(rotated["access_token"]).credential == family
            core.auth._issue("test-client", core.cfg.resource, "second")
    assert (await subscribe(client, params(ttlMs=20_000), token=token))["result"]["id"]
    await send(core)
    await eventually(lambda: len(receiver.calls) == 2 and pending(core)[0]["attempts"] == 1)
    if change == "replace_key":
        core.cfg.static_keys = tuple(replace(key, secret="replacement") if key.name == "second" else key for key in core.cfg.static_keys)
    elif change == "connect":
        core.connections.revoke(key_id=key_id)
    elif change == "oauth":
        core.auth.revoke(token, "test-client")
    elif change == "remove_oauth_client":
        core.store.x("DELETE FROM oauth_clients WHERE client_id = ?", ("test-client",))
    elif change == "dns":
        receiver.addresses = ["127.0.0.1"]
    clock.t += 21 if change == "expiry" else 15
    worker.wake.set()
    await eventually(lambda: not pending(core))
    assert len(receiver.calls) == 2
    if change == "dns":
        assert subscription(core)["last_status"] == "blocked_destination"
    else:
        assert subscription(core)["active"] == 0
        assert core.audit.recent(action="events_deactivate")[0]["ref"] == subscription(core)["id"]


async def test_unsubscribe_is_idempotent_and_scoped_to_the_client(amake):
    core, client = await amake()
    receiver = Receiver()
    receiver.answers = [503]
    receiver.bind(core)
    sid = (await subscribe(client))["result"]["id"]
    await send(core)
    await eventually(lambda: len(receiver.calls) == 2 and pending(core)[0]["attempts"] == 1)
    assert (await rpc(client, "events/unsubscribe", {"id": sid}, token=STATIC_KEY))["result"] == {}
    assert subscription(core) and pending(core)
    selector = params()
    del selector["delivery"]["secret"]
    del selector["cursor"]
    assert (await rpc(client, "events/unsubscribe", {**selector, "id": sid}))["result"] == {}
    assert not subscription(core) and not pending(core)
    assert (await rpc(client, "events/unsubscribe", selector))["result"] == {}
    assert (await rpc(client, "events/unsubscribe", {"id": sid}))["result"] == {}
    assert len(receiver.calls) == 2
