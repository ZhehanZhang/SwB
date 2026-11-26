"""The Slack port inside switchboard: slack_* tools over switchboard auth, events into each granted agent's inbox,
and owner approvals on the root page. Agents never approve their own writes."""

import json
import re

import httpx
import pytest
import yaml
from starlette.testclient import TestClient

from slackfix import ENV, KEY_FIXED, KEY_FULL, FakeWorld, Hooks, base_config, payload
from switchboard.app import create_app
from switchboard.core import Switchboard

PUBLIC = "https://switchboard.example.test"
OWNER = {"cf-access-jwt-assertion": "good"}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


async def access(assertion):
    return {"good": "owner@example.test", "other": "someone@example.test"}.get(assertion)


@pytest.fixture
def sb(tmp_path):
    world, hooks = FakeWorld(), Hooks()
    cfg = {"public_url": PUBLIC, "auth": {"owner_emails": ["owner@example.test"], "cloudflare_access": {"team_domain": "z.cloudflareaccess.com", "aud": "a"}},
           "slack": base_config()}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    env = {**ENV, "SWITCHBOARD_KEY_FULL": KEY_FULL, "SWITCHBOARD_KEY_FIXED": KEY_FIXED, "SWITCHBOARD_KEY_NOBODY": "n" * 32}
    core = Switchboard(path, tmp_path / "data", env=env, access_verifier=access,
                       slack_options={"client_factory": world.factory, "http": httpx.AsyncClient(transport=httpx.MockTransport(hooks.handler)),
                                      "own_post_grace": 0, "connect": False})
    with TestClient(create_app(core), base_url=PUBLIC, follow_redirects=False) as client:
        yield core, client, world


def rpc(client, key, method, params=None):
    return client.post("/mcp", headers={**HEADERS, "Authorization": f"Bearer {key}"}, content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}))


def tool(client, key, name, args=None):
    r = rpc(client, key, "tools/call", {"name": name, "arguments": args or {}})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    return res, (None if res.get("isError") else json.loads(res["content"][0]["text"]) if res["content"] else None)


def feed(core, client, label, name):
    p = payload(name)
    client.portal.call(core.slack.handle_socket, label, p["type"], p["payload"])


def test_slack_tools_use_switchboard_keys_and_grants(sb):
    core, client, world = sb
    names = {t["name"] for t in rpc(client, KEY_FULL, "tools/list").json()["result"]["tools"]}
    assert {"slack_workspaces", "slack_post", "slack_history", "slack_wait", "slack_request_write", "slack_write_requests"} <= names
    assert not {n for n in names if any(w in n for w in ("approve", "reject", "decide"))}
    assert rpc(client, "wrong" * 8, "tools/list").status_code == 401
    _, me = tool(client, KEY_FIXED, "slack_workspaces")
    assert me["you"]["agent"] == "fixed" and [w["workspace"] for w in me["workspaces"]] == ["a2a"]
    res, _ = tool(client, "n" * 32, "slack_workspaces")
    assert res["isError"] and "no Slack access" in res["content"][0]["text"]
    res, _ = tool(client, KEY_FIXED, "slack_post", {"workspace": "private", "channel": "CPRIV0001", "text": "x"})
    assert res["isError"]
    _, pending = tool(client, KEY_FULL, "slack_post", {"workspace": "private", "channel": "#family", "text": "hi", "identity": "user"})
    assert pending["status"] == "pending_approval"
    _, sent = tool(client, KEY_FIXED, "slack_post", {"workspace": "a2a", "channel": "CWATCH001", "text": "yo", "thread_ts": "1700000000.000001"})
    assert sent["status"] == "sent"
    rows = {(r["source"], r["action"], r["outcome"]) for r in core.audit.recent(destination="slack", limit=10)}
    assert ("fixed", "post", "ok") in rows and ("full", "post", "queued") in rows
    health = client.get("/healthz").json()
    assert "slack" in health["origins"] and health["destinations"]["slack"]["online"] and health["destinations"]["slack"]["pending_approvals"] == 1
    assert not [v for v in ENV.values() if v in json.dumps(health)]


def test_events_reach_only_granted_agents_with_slack_attribution(sb):
    core, client, _ = sb
    feed(core, client, "a2a", "a_app_mention.json")
    feed(core, client, "private", "b_user_event_watched.json")
    _, got = tool(client, KEY_FIXED, "slack_wait", {"timeout_s": 0})
    assert [(i["kind"], i["from"]["workspace"], i["slack_kind"]) for i in got["items"]] == [("slack_event", "a2a", "mention")]
    item = got["items"][0]
    assert item["addressed_to_us"] and item["from"]["kind"] == "slack" and item["from"]["channel"] == "CPUBLIC01" and item["from"]["user_name"]
    _, full = tool(client, KEY_FULL, "slack_wait", {"timeout_s": 0})
    assert sorted(i["from"]["workspace"] for i in full["items"]) == ["a2a", "private"]
    _, again = tool(client, KEY_FIXED, "slack_wait", {"timeout_s": 0})
    assert again["items"] == [] and again["timed_out"]


def test_owner_approves_on_the_root_page_and_agents_cannot(sb):
    core, client, world = sb
    _, pending = tool(client, KEY_FULL, "slack_post", {"workspace": "private", "channel": "CPRIV0001", "text": "see you <b>soon</b>", "identity": "user"})
    rid = pending["request_id"]
    assert client.get("/").status_code == 403 and client.get("/", headers={"cf-access-jwt-assertion": "other"}).status_code == 403
    page = client.get("/", headers=OWNER).text
    assert rid in page and "Approve and send" in page and "see you &lt;b&gt;soon&lt;/b&gt;" in page
    token = re.search(r'name="csrf" value="([0-9a-f]+)"', page).group(1)
    assert client.post(f"/slack/requests/{rid}/approve", headers=OWNER, data={"csrf": "0" * 64}).status_code == 403
    assert client.post(f"/slack/requests/{rid}/approve", headers={"cf-access-jwt-assertion": "other"}, data={"csrf": token}).status_code == 403
    assert client.post(f"/slack/requests/{rid}/approve", headers={"Authorization": f"Bearer {KEY_FULL}"}, data={"csrf": token}).status_code == 403
    assert core.slack.store.get_request(rid)["status"] == "pending"
    r = client.post(f"/slack/requests/{rid}/approve", headers=OWNER, data={"csrf": token})
    assert r.status_code == 303 and r.headers["location"] == "/?notice=approved"
    req = core.slack.store.get_request(rid)
    assert req["status"] == "sent" and req["decided_by"] == "admin:owner@example.test"
    assert [kw["text"] for t, n, kw in world.calls if t == "xoxp-b" and n == "chat_postMessage"] == ["see you <b>soon</b>"]
    assert "Approved and sent." in client.get("/?notice=approved", headers=OWNER).text


def test_captain_final_reply_is_posted_back_as_the_captain(sb):
    from switchboard.slack.config import Persona
    from switchboard.slack.routing import build_targets

    core, client, world = sb
    names = {t["name"] for t in rpc(client, KEY_FULL, "tools/list").json()["result"]["tools"]}
    assert "slack_targets" in names
    core.slack.set_targets(build_targets([{"id": "jam_P", "name": "TeamCaptain"}], [], []), {"teamcaptain": Persona(id="teamcaptain", name="TeamCaptain")})
    core.slack.store.add_relay("h_test", "a2a", "CPUBLIC01", "1700000000.000001", "teamcaptain")
    origin = core.origins["slack"]
    client.portal.call(origin.relay, {"kind": "reply", "handle": "h_test", "text": "interim"})
    client.portal.call(origin.relay, {"kind": "final", "handle": "h_test", "text": "all done"})
    client.portal.call(origin.relay, {"kind": "final", "handle": "h_other", "text": "not ours"})
    posts = [kw for t, n, kw in world.calls if n == "chat_postMessage"]
    assert [(p["text"], p["username"], p["thread_ts"]) for p in posts] == [("all done", "TeamCaptain", "1700000000.000001")]
    _, got = tool(client, KEY_FULL, "slack_targets")
    assert any(t["slug"] == "teamcaptain" and t["kind"] == "captain" for t in got["targets"])

