import asyncio
import json
from urllib.parse import urlparse
from uuid import UUID

import httpx
import pytest

from conftest import OWNER, PUBLIC, SECOND_KEY, STATIC_KEY, base_config, call, error_text, payload
from switchboard.auth import Principal
from switchboard.blocks import BlockError
from switchboard.claims import ClaimError
from switchboard.muse_gadget import BridgeOffline, relay
from switchboard.muse_protocol import BRIDGE_PATH, DELIVERY_TIMEOUT

SECRET = "a" * 43
REPLACEMENT = "b" * 43
CLAIM = "a" * 32
OTHER = "b" * 32
ADDR = "muse:gadget:muse"


def make_muse(make, *, configured=True, public_url=PUBLIC):
    cfg = base_config(version=2, public_url=public_url, blocks={"mcp": {"use": "mcp"}, "muse": {"use": "muse"}})
    env = {"SWITCHBOARD_KEY_OWNER": STATIC_KEY, "SWITCHBOARD_KEY_SECOND": SECOND_KEY}
    return make(cfg, {**env, **({"MUSE_BRIDGE_TOKEN": SECRET} if configured else {})})


def bridge(client, claim=CLAIM, token=SECRET, **data):
    return client.post(BRIDGE_PATH, headers={"Authorization": f"Bearer {token}"}, json={"claim": claim, **data})


@pytest.mark.parametrize("url", ["http://localhost:18902", "http://127.0.0.1:18902", "http://[::1]:18902", PUBLIC])
def test_muse_connect_hands_the_linux_device_a_reachable_address(make, url):
    _, client = make_muse(make, public_url=url)
    setup = payload(call(client, STATIC_KEY, "connect", {"what": "muse"}))
    assert all(step["by"] == "person" for step in setup["next"])
    actions = [step["do"] for step in setup["next"]]
    if url == PUBLIC:
        command = f"Run swb muse-bridge --url {url} --setup on the Linux device."
        assert command in actions
        assert actions[actions.index(command) + 1:actions.index(command) + 3] == [
            "Enter the SwB bridge token at its hidden prompt.", "Enter the personal Meta token at its hidden prompt.",
        ]
        assert "Select Add Device." in actions and "Select the Bluetooth name SwB prints." in actions
    else:
        assert actions[0] == "Stop SwB on its computer."
        assert "swb up --tunnel cloudflare" in actions[1] and "Linux device" in actions[1]
        assert "swb muse-bridge" not in json.dumps(setup) and url not in json.dumps(setup)


def test_muse_connect_claim_status_and_credential_scope(make):
    core, client = make_muse(make, configured=False)
    status = payload(call(client, STATIC_KEY, "switchboard_status"))
    assert status["blocks"]["muse"]["online"] is False and "claim link" in status["blocks"]["muse"]["next_step"]
    setup = payload(call(client, STATIC_KEY, "connect", {"what": "muse"}))
    assert setup["done"] == ["Muse bridge setup is ready."]
    path = urlparse(setup["next"][1]["link"]).path
    assert path.startswith("/claim/")
    refused = client.get(path)
    assert refused.status_code == 303 and refused.headers["location"].startswith("/login?next=%2Fclaim%2F")
    refused = client.post(path, data={"token": SECRET})
    assert refused.status_code == 401 and refused.json() == {"error": "signed_out"}
    assert core.secret("MUSE_BRIDGE_TOKEN") is None
    page = client.get(path, headers=OWNER)
    assert 'data-connector="muse"' in page.text and 'src="/avatars/muse.png"' in page.text
    sprite = client.get("/assets/claim-connectors.svg")
    assert sprite.status_code == 200 and '<symbol id="muse"' not in sprite.text
    [pending] = client.get("/api/tokens", headers=OWNER).json()["waiting"]
    assert (pending["label"], pending["kind"]) == ("MUSE_BRIDGE_TOKEN", "muse")
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(path, data={"csrf": csrf, "token": SECRET}, headers=OWNER)
    assert response.status_code == 200 and SECRET not in response.text
    assert core.status()["destinations"]["muse"]["state"] == "offline"
    assert bridge(client, token=STATIC_KEY).status_code == 401
    assert bridge(client, claim="invalid").status_code == 400
    assert bridge(client).json() == {"message": None}
    assert client.get("/inbox/poll", headers={"Authorization": f"Bearer {SECRET}"}).status_code == 401
    assert client.post("/mcp", headers={"Authorization": f"Bearer {SECRET}"}).status_code == 401
    setup = payload(call(client, STATIC_KEY, "connect", {"what": "muse"}))
    assert setup["next"][0]["call"]["arguments"]["to"] == ADDR
    assert "state" not in setup and SECRET not in json.dumps(core.status())
    rotate = payload(call(client, STATIC_KEY, "connect", {"what": "muse", "choose": {"replace": True}}))
    code = urlparse(rotate["next"][1]["link"]).path.rsplit("/", 1)[1]
    with pytest.raises(ClaimError, match="bridge token"):
        core.claims.consume(code, "mistyped")
    assert bridge(client).is_success
    core.claims.consume(code, REPLACEMENT)
    assert core.status()["destinations"]["muse"]["state"] == "offline"
    assert bridge(client).status_code == 401 and bridge(client, token=REPLACEMENT).is_success


def test_muse_mailbox_pickup_and_reply_are_deduped(make):
    core, client = make_muse(make)
    targets = payload(call(client, STATIC_KEY, "targets", {"block": "muse"}))
    assert ADDR in json.dumps(targets)
    avatar = client.get("/avatars/muse.png")
    assert avatar.status_code == 200 and avatar.headers["content-type"] == "image/png"
    send = {"to": "muse", "task": "Reply with pong."}
    first = payload(call(client, STATIC_KEY, "send", send))
    duplicate = payload(call(client, STATIC_KEY, "send", {**send, "to": ADDR}))
    other = payload(call(client, SECOND_KEY, "send", send))
    plain = payload(call(client, STATIC_KEY, "send", {**send, "to": "agent:muse"}))
    assert first["queued"] and first["mailbox"] and duplicate["duplicate"]
    handle = first["conversation"]
    assert duplicate["conversation"] == handle and other["conversation"] != handle and plain["conversation"] != handle

    work = bridge(client).json()["message"]
    assert work["handle"] == handle and work["text"] == send["task"]
    assert UUID(work["session_id"]).version == 4
    assert bridge(client).json()["message"] == work
    second = bridge(client, OTHER).json()["message"]
    assert second["handle"] == other["conversation"] and second["session_id"] != work["session_id"]
    assert bridge(client, "c" * 32).json() == {"message": None}
    assert bridge(client, OTHER, handle=handle, state="answered", text="wrong").status_code == 404
    result = {"handle": handle, "state": "answered", "text": "pong"}
    assert bridge(client, **result).is_success
    assert bridge(client, **result).is_success
    assert bridge(client, token=SECRET, **{**result, "text": "duplicate"}).is_success
    answer = payload(call(client, STATIC_KEY, "result", {"conversation": handle}))
    assert answer["status"] == "answered" and len(answer["replies"]) == 1
    assert answer["final"]["text"] == "pong" and answer["final"]["from"]["agent"] == "muse"
    assert payload(call(client, STATIC_KEY, "connect", {"what": "muse"}))["state"] == "connected"
    assert core.status()["destinations"]["muse"]["verified"]


def test_muse_uncertain_delivery_is_not_replayed(make):
    core, client = make_muse(make)
    send = {"to": ADDR, "task": "wake once"}
    handle = payload(call(client, STATIC_KEY, "send", send))["conversation"]
    assert bridge(client).json()["message"]["handle"] == handle
    core.clock.t += DELIVERY_TIMEOUT + 1
    assert bridge(client, OTHER).json() == {"message": None}
    assert bridge(client).json() == {"message": None}
    assert payload(call(client, STATIC_KEY, "result", {"conversation": handle}))["status"] == "delivery_unknown"
    assert payload(call(client, STATIC_KEY, "send", send))["duplicate"]
    assert bridge(client, handle=handle, state="answered", text="late answer").is_success
    assert payload(call(client, STATIC_KEY, "result", {"conversation": handle}))["final"]["text"] == "late answer"


@pytest.mark.asyncio
async def test_muse_routes_require_owner_trust_and_explicit_slot(make):
    core, _ = make_muse(make)
    owner = Principal("owner", "owner", "key")
    with pytest.raises(BlockError, match="Owner|owner"):
        await core.send(Principal("guest", "guest", "slack", trust="known"), task="no", to="muse")
    with pytest.raises(BlockError, match="Name Muse explicitly"):
        await core.send(owner, task="unrouted")
    with pytest.raises(BlockError, match="Muse send scope"):
        await core.send(Principal("owner", "scoped", "key", scopes=("project:demo",)), task="no", to="muse")
    core.projects.create(slug="demo", name="Demo")
    await core.set_route(owner, destination="muse", agent="owner", project="demo", target={"gadget": "muse"}, defaults={}, allow_override=False)
    queued = await core.send(owner, task="routed", project="demo")
    assert queued["to"] == ADDR and queued["project"] == "demo"


def test_default_paired_key_does_not_wake_the_owners_muse(make):
    core, client = make_muse(make)
    code = payload(call(client, STATIC_KEY, "connect_code", {"agent": "paired"}))["code"]
    key = client.post("/connect", json={"code": code}).json()["key"]
    args = {"to": ADDR, "task": "Check the example"}
    assert error_text(call(client, key, "send", args)) == "Use the owner's sign-in to call this tool."
    [row] = core.audit.recent(action="tool_refused")
    assert (row["target"], row["detail"]) == ("send", "Use the owner's sign-in to call this tool.")
    assert args["task"] not in str(row) and key[:12] not in str(row)
    assert core.inbox.head() == 0
    assert payload(call(client, STATIC_KEY, "send", args))["mailbox"]


@pytest.mark.asyncio
async def test_gadget_sends_through_sdk_then_posts_correlated_reply(tmp_path):
    requested = []
    finish = asyncio.Event()

    def server(request):
        data = json.loads(request.content)
        requested.append(data)
        assert request.url.path == BRIDGE_PATH
        if "handle" not in data:
            return httpx.Response(200, json={"message": None if finish.is_set() else
                                           {"handle": "h_original", "session_id": "session-1", "text": "text"}})
        finish.set()
        return httpx.Response(200, json={"ok": True})

    class Session:
        registered = asyncio.Event()

        async def send_chat(self, text, session_id):
            assert (text, session_id) == ("text", "session-1")
            return {"ok": True}

    async def sdk(session, send, session_id, timeout):
        assert session.registered.is_set() and session_id == "session-1" and timeout > 0
        return {**await send(), "reply": "pong"}

    session = Session()
    async with httpx.AsyncClient(transport=httpx.MockTransport(server), base_url="https://swb.test") as client:
        job = asyncio.create_task(relay(session, client, BRIDGE_PATH, sdk, tmp_path / "pending.json"))
        await asyncio.sleep(0)
        assert not requested
        session.registered.set()
        await asyncio.wait_for(finish.wait(), 2)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
    assert requested[1] == {"claim": requested[0]["claim"], "handle": "h_original", "state": "answered", "text": "pong"}


@pytest.mark.asyncio
async def test_gadget_replays_saved_callback_after_outage_and_restart(tmp_path, monkeypatch):
    calls = []
    failed = asyncio.Event()
    accepted = asyncio.Event()
    running = asyncio.Event()
    pending = tmp_path / "pending.json"
    wake_count = 0

    class Session:
        registered = asyncio.Event()

        async def send_chat(self, text, session_id):
            nonlocal wake_count
            wake_count += 1
            return {"ok": True}

    async def sdk(session, send, _session_id, _timeout):
        return {**await send(), "reply": "first reply"}

    async def exchange(_client, _url, request):
        calls.append(request.copy())
        if "handle" in request:
            if not running.is_set():
                failed.set()
                raise BridgeOffline
            accepted.set()
            return {"ok": True}
        if len(calls) > 1:
            return {"message": None}
        return {"message": {"handle": "h_original", "session_id": "session", "text": "wake"}}

    monkeypatch.setattr("switchboard.muse_gadget.exchange", exchange)
    s = Session()
    s.registered.set()
    job = asyncio.create_task(relay(s, None, BRIDGE_PATH, sdk, pending))
    await asyncio.wait_for(failed.wait(), timeout=2)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert pending.stat().st_mode & 0o777 == 0o600
    assert json.loads(pending.read_text())["result"]["text"] == "first reply"

    running.set()
    job = asyncio.create_task(relay(s, None, BRIDGE_PATH, sdk, pending))
    await asyncio.wait_for(accepted.wait(), timeout=2)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert calls[1] == calls[2]
    assert wake_count == 1
    assert not pending.exists()
