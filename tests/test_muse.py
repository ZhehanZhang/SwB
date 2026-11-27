import asyncio
import json
from urllib.parse import urlparse
from uuid import UUID

import httpx
import pytest

from conftest import OWNER, SECOND_KEY, STATIC_KEY, base_config, call, payload
from switchboard.auth import Principal
from switchboard.blocks import BlockError
from switchboard.muse_gadget import relay
from switchboard.muse_protocol import BRIDGE_PATH, DELIVERY_TIMEOUT

SECRET = "a" * 43
REPLACEMENT = "b" * 43
CLAIM = "a" * 32
OTHER = "b" * 32
ADDR = "muse:gadget:muse"


def make_muse(make, *, configured=True):
    cfg = base_config(version=2, blocks={"mcp": {"use": "mcp"}, "muse": {"use": "muse"}})
    env = {"SWITCHBOARD_KEY_OWNER": STATIC_KEY, "SWITCHBOARD_KEY_SECOND": SECOND_KEY}
    return make(cfg, {**env, **({"MUSE_BRIDGE_TOKEN": SECRET} if configured else {})})


def bridge(client, claim=CLAIM, token=SECRET, **data):
    return client.post(BRIDGE_PATH, headers={"Authorization": f"Bearer {token}"}, json={"claim": claim, **data})


def test_muse_connect_claim_status_and_credential_scope(make):
    core, client = make_muse(make, configured=False)
    status = payload(call(client, STATIC_KEY, "switchboard_status"))
    assert status["blocks"]["muse"]["online"] is False and "claim link" in status["blocks"]["muse"]["next_step"]
    setup = payload(call(client, STATIC_KEY, "connect", {"what": "muse"}))
    assert setup["done"] == ["Muse bridge: unconfigured."]
    path = urlparse(setup["next"][0]["link"]).path
    assert path.startswith("/claim/")
    assert client.get(path).status_code == 403
    page = client.get(path, headers=OWNER)
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
    core.claims.consume(urlparse(rotate["next"][0]["link"]).path.rsplit("/", 1)[1], REPLACEMENT)
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
    with pytest.raises(BlockError, match="cannot send to Muse in this scope"):
        await core.send(Principal("owner", "scoped", "key", scopes=("project:demo",)), task="no", to="muse")
    core.projects.create(slug="demo", name="Demo")
    await core.set_route(owner, destination="muse", agent="owner", project="demo", target={"gadget": "muse"}, defaults={}, allow_override=False)
    queued = await core.send(owner, task="routed", project="demo")
    assert queued["to"] == ADDR and queued["project"] == "demo"


@pytest.mark.asyncio
async def test_gadget_sends_through_sdk_then_posts_correlated_reply():
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
        job = asyncio.create_task(relay(session, client, BRIDGE_PATH, sdk))
        await asyncio.sleep(0)
        assert not requested
        session.registered.set()
        await asyncio.wait_for(finish.wait(), 2)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
    assert requested[1] == {"claim": requested[0]["claim"], "handle": "h_original", "state": "answered", "text": "pong"}
