import re

import pytest

from conftest import CAPTAIN, CAPTAIN2, CAPTAIN_CODE, CAPY_KEY, OWNER, PUBLIC, SERVICE_KEY, STATIC_KEY, access_verifier, base_config, default_env
from switchboard.blocks import Action
from switchboard.slack.writes import WriteError


EMAIL = "owner@example.test"
SECOND_OWNER = "second@example.test"
ROUTE = {"destination": "capy", "target": {"captain": CAPTAIN_CODE}, "agent": "claude",
         "defaults": {"delivery": "queue"}, "allow_override": True, "note": "Owner route"}
VISIBILITY = {"resource": "target:claude", "visibility": ["UOWNER01", "workspace:example"]}
SEND = {"task": "Check the route", "to": "agent:claude"}
ROUTINE_TOKEN = "sk-ant-oat01-" + "t" * 20
SLACK_SECRET = "upstream-secret-for-test"


@pytest.fixture
def board(make):
    async def access(assertion):
        return SECOND_OWNER if assertion == "second-owner" else await access_verifier(assertion)

    auth = base_config()["auth"]
    auth["owner_emails"].append(SECOND_OWNER)
    return make(config=base_config(version=2, auth=auth, blocks={
        "mcp": {},
        "capy": {"daily_cap": 1},
        "claude-routine": {"routines": {"fixed": {"routine_id": "trig_CONFIGURED", "token_env": "FIXED_ROUTINE_TOKEN"}}},
        "grok-routine": {"routines": {"grok-test": {"url_env": "GROK_TEST_URL", "secret_env": "GROK_TEST_KEY"}}},
        "dots": {"delivery": "mailbox", "dots": {"example": {"mailbox": "claude", "user_id": "UEXAMPLE1"}}},
        "slack": {"workspaces": {"example": {"team_id": "TEXAMPLE01", "intake": "none",
                                             "identities": {"bot": {"token_env": "SLACK_EXAMPLE_BOT_TOKEN"}}}}},
    }), env={**default_env(), "FIXED_ROUTINE_TOKEN": ROUTINE_TOKEN, "GROK_TEST_URL": "https://example.test/secret-hook",
             "GROK_TEST_KEY": SLACK_SECRET}, access=access)


def owner_headers(client):
    response = client.get("/auth/session", headers=OWNER)
    assert response.status_code == 200
    session = response.json()
    assert session["signed_in"] and session["via"] == "access" and session["csrf"]
    return {**OWNER, "Origin": PUBLIC, "X-CSRF-Token": session["csrf"]}


@pytest.mark.parametrize(("method", "path", "body"), [
    ("POST", "/api/routes", ROUTE),
    ("DELETE", "/api/routes/1", None),
    ("POST", "/api/visibility", VISIBILITY),
    ("POST", "/api/pairing/code", {"agent": "codex"}),
    ("POST", "/api/pairing/keys/ak_unknown/revoke", None),
    ("POST", "/api/routines/unknown/remove", None),
    ("POST", "/api/slack/requests/wr_unknown/approve", None),
    ("POST", "/api/slack/requests/wr_unknown/reject", None),
    ("POST", "/api/send", {**SEND, "dry_run": True}),
])
def test_each_write_requires_owner_origin_and_session_csrf(board, method, path, body):
    _, client = board
    headers = owner_headers(client)
    attempts = [
        {"Origin": PUBLIC, "X-CSRF-Token": headers["X-CSRF-Token"]},
        {**headers, "cf-access-jwt-assertion": "other"},
        {"Authorization": f"Bearer {STATIC_KEY}", "Origin": PUBLIC, "X-CSRF-Token": headers["X-CSRF-Token"]},
        OWNER,
        {**OWNER, "Origin": PUBLIC},
        {**headers, "Origin": "http://switchboard.example.test"},
        {**headers, "Origin": "https://other.example.test"},
        {**headers, "X-CSRF-Token": "not-the-session-token"},
    ]
    for i, attempted in enumerate(attempts):
        response = client.request(method, path, headers=attempted, json=body)
        assert response.status_code == (401 if i < 3 else 403)
        assert response.json() == {"error": "signed_out" if i < 3 else "csrf"}
        assert headers["X-CSRF-Token"] not in response.text and STATIC_KEY not in response.text


def test_csrf_is_bound_to_the_authenticated_access_identity(board):
    core, client = board
    headers = owner_headers(client)
    other = {**headers, "cf-access-jwt-assertion": "second-owner"}
    response = client.post("/api/visibility", headers=other, json=VISIBILITY)
    assert response.status_code == 403 and core.visibility.explicit(VISIBILITY["resource"]) is None
    session = client.get("/auth/session", headers=other).json()
    assert session["signed_in"] and session["csrf"] != headers["X-CSRF-Token"]
    response = client.post("/api/visibility", headers={**other, "X-CSRF-Token": session["csrf"]}, json=VISIBILITY)
    assert response.status_code == 200


def test_route_write_and_unplug_use_the_canonical_route(board):
    core, client = board
    headers = owner_headers(client)
    response = client.post("/api/routes", headers=headers, json=ROUTE)
    assert response.status_code == 200
    route = core.routes.list()[0].as_dict()
    assert response.json() == {"id": route["id"]}
    assert route["target"]["captain"] == CAPTAIN
    assert route["updated_by"] == f"admin:{EMAIL}"
    assert route["level"] == "agent" and route["defaults"] == ROUTE["defaults"]
    assert all(secret not in response.text for secret in (STATIC_KEY, CAPY_KEY, SERVICE_KEY))
    response = client.delete(f"/api/routes/{route['id']}", headers=headers)
    assert response.status_code == 200 and response.json() == {"id": route["id"], "removed": True}
    assert core.routes.list() == []
    assert core.audit.recent(action="route_set")[0]["source"] == f"admin:{EMAIL}"
    assert core.audit.recent(action="route_remove")[0]["source"] == f"admin:{EMAIL}"
    assert client.delete(f"/api/routes/{route['id']}", headers=headers).status_code == 404


def test_unplug_does_not_echo_unvalidated_config_route_defaults(board):
    core, client = board
    route = core.routes.set(destination="capy", agent=None, project=None, target={"captain": CAPTAIN}, defaults={"token": SLACK_SECRET},
                            allow_override=False, note="seeded", by="config")
    response = client.delete(f"/api/routes/{route.id}", headers=owner_headers(client))
    assert response.status_code == 200 and response.json() == {"id": route.id, "removed": True}
    assert SLACK_SECRET not in response.text


def test_visibility_writes_the_resolver_rule(board):
    core, client = board
    headers = owner_headers(client)
    response = client.post("/api/visibility", headers=headers, json=VISIBILITY)
    assert response.status_code == 200 and response.json() == VISIBILITY
    assert core.visibility.explicit(VISIBILITY["resource"]) == tuple(VISIBILITY["visibility"])
    response = client.post("/api/visibility", headers=headers, json={"resource": "tool:send", "visibility": "shared"})
    assert response.status_code == 400 and response.json() == {"error": "visibility_not_saved"}
    assert core.visibility.rule("tool:send") == "private"


def test_pairing_code_uses_the_protected_classic_handoff(board):
    core, client = board
    headers = owner_headers(client)
    response = client.post("/api/pairing/code", headers=headers, json={"agent": "codex"})
    assert response.status_code == 200
    assert response.json() == {"status": "classic_required", "url": "/classic"}
    assert response.headers["Cache-Control"] == "no-store"
    assert core.store.q("SELECT 1 FROM connect_codes") == []
    denied = client.get(response.json()["url"])
    assert denied.status_code == 303 and denied.headers["location"] == "/login?next=%2Fclassic"
    classic = client.get(response.json()["url"], headers=OWNER)
    assert classic.status_code == 200 and 'action="/pairing/code"' in classic.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', classic.text)[1]
    assert client.post("/pairing/code", headers=OWNER, data={"agent": "codex"}).status_code == 403
    minted = client.post("/pairing/code", headers=OWNER, data={"agent": "codex", "csrf": csrf})
    assert minted.status_code == 200
    codes = core.store.q("SELECT agent FROM connect_codes")
    assert [row["agent"] for row in codes] == ["codex"]
    assert len(re.findall(r"[A-Z2-9]{5}-[A-Z2-9]{5}", minted.text)) >= 1


def test_key_revoke_returns_only_id_and_status(board):
    core, client = board
    headers = owner_headers(client)
    code = core.connections.mint(agent="codex", project=None, client=None, by="test")
    key = core.connections.redeem(code["code"])
    assert core.auth.verify_bearer(key["key"]) is not None
    path = f"/api/pairing/keys/{key['key_id']}/revoke"
    response = client.post(path, headers=headers)
    assert response.status_code == 200 and response.json() == {"id": key["key_id"], "revoked": True}
    assert key["key"] not in response.text and code["code"] not in response.text
    assert core.auth.verify_bearer(key["key"]) is None
    assert client.post(path, headers=headers).status_code == 404


def test_remove_routine_uses_storage_and_preserves_configured_ones(board):
    core, client = board
    headers = owner_headers(client)
    routine = core.blocks["claude-routine"]
    client.portal.call(lambda: routine.add(fire="trig_TESTROUTINE", token=ROUTINE_TOKEN, name="disposable",
                                           project=None, agent="claude", actor="test"))
    response = client.post("/api/routines/disposable/remove", headers=headers)
    assert response.status_code == 200 and response.json() == {"name": "disposable", "removed": True}
    assert ROUTINE_TOKEN not in response.text
    assert "disposable" not in routine.routines()
    assert client.post("/api/routines/disposable/remove", headers=headers).status_code == 404
    response = client.post("/api/routines/fixed/remove", headers=headers)
    assert response.status_code == 400 and response.json() == {"error": "routine_not_removed"}
    assert "fixed" in routine.routines()


@pytest.mark.parametrize(("verb", "status"), [("reject", "rejected"), ("approve", "sent"), ("approve", "failed")])
def test_slack_decisions_use_the_gate_and_return_only_a_receipt(board, monkeypatch, verb, status):
    core, client = board
    headers = owner_headers(client)
    slack = core.slack
    pending = slack.store.create_request(workspace="example", identity="bot", action="post_message",
                                         params={"channel": "CEXAMPLE1", "text": SLACK_SECRET}, persona=None,
                                         key_id="configured-key-name", ttl_seconds=60)
    calls = []

    async def execute(*args, **kwargs):
        calls.append(args)
        if status == "failed":
            raise WriteError(SLACK_SECRET)
        return {"ts": "1.2", "upstream": SLACK_SECRET}

    monkeypatch.setattr(slack.gate, "_execute", execute)
    path = f"/api/slack/requests/{pending['id']}"
    assert client.post(f"{path}/publish", headers=headers).status_code == 404
    response = client.post(f"{path}/{verb}", headers=headers)
    assert response.status_code == 200 and response.json() == {"id": pending["id"], "status": status}
    assert SLACK_SECRET not in response.text
    assert bool(calls) == (verb == "approve")
    final = slack.store.get_request(pending["id"])
    assert final["status"] == status and final["decided_by"] == f"admin:{EMAIL}"
    response = client.post(f"{path}/{verb}", headers=headers)
    assert response.status_code == 409 and response.json() == {"error": "decision_refused"}


def test_send_preview_does_not_dispatch_and_actual_send_uses_core(board, capy):
    core, client = board
    headers = owner_headers(client)
    before = list(capy.requests)
    response = client.post("/api/send", headers=headers, json={**SEND, "dry_run": True})
    assert response.status_code == 200
    assert response.json()["dry_run"] is True and response.json()["to"] == SEND["to"]
    assert "handle" not in response.json() and SEND["task"] not in response.text
    assert capy.requests == before
    assert not core.inbox.handles(owner="claude")
    assert core.audit.recent(action="agent_send") == []
    response = client.post("/api/send", headers=headers, json=SEND)
    assert response.status_code == 200
    receipt = response.json()
    assert receipt["to"] == SEND["to"] and receipt["mailbox"] and receipt["delivered"]
    assert receipt["handle"] == receipt["conversation"]
    assert SEND["task"] not in response.text and STATIC_KEY not in response.text
    handle = core.inbox.handle(receipt["handle"])
    assert handle["owner"] == "claude" and handle["ref"] == f"admin:{EMAIL}"


def test_send_preview_uses_real_capy_route_and_override_rules(board, capy):
    core, client = board
    headers = owner_headers(client)
    core.projects.create(slug="example", name="Example")
    route = client.post("/api/routes", headers=headers,
                        json={**ROUTE, "project": "example", "agent": None, "allow_override": False})
    assert route.status_code == 200
    request = {"task": "Send to the project route", "to": "project:example"}
    capy_before = list(capy.requests)
    response = client.post("/api/send", headers=headers, json={**request, "dry_run": True})
    assert response.status_code == 200
    assert response.json() == {"dry_run": True, "to": f"capy:captain:{CAPTAIN}", "block": "capy",
                               "project": "example", "route": "project", "mode": "captain_thread"}
    refused = client.post("/api/send", headers=headers,
                          json={**request, "to": f"capy:captain:{CAPTAIN2}", "project": "example", "dry_run": True})
    assert refused.status_code == 400 and refused.json() == {"error": "send_refused"}
    assert capy.requests == capy_before
    assert core.audit.recent(action="delegate") == []
    assert core.store.q("SELECT handle FROM handles") == []

    sent = client.post("/api/send", headers=headers, json=request)
    assert sent.status_code == 200 and sent.json()["thread_id"] == CAPTAIN
    assert sent.json()["route"] == response.json()["route"]
    assert core.audit.count_today("capy", "delegate") == 1
    blocked = client.post("/api/send", headers=headers, json={**request, "dry_run": True})
    assert blocked.status_code == 400
    assert len(core.audit.recent(action="delegate")) == 1


@pytest.mark.parametrize(("target", "resolved", "mailbox"), [
    ("claude-routine:routine:fixed", "claude-routine:routine:fixed", "claude"),
    ("grok-routine:routine:grok-test", "grok-routine:routine:grok-test", "grok"),
    ("dots:dot:example", "dots:dot:UEXAMPLE1", "claude"),
])
def test_send_previews_block_targets_without_delivery(board, monkeypatch, target, resolved, mailbox):
    core, client = board

    async def no_delivery(*args, **kwargs):
        pytest.fail("dry-run dispatched a message")

    monkeypatch.setattr(core.blocks["claude-routine"].http, "post", no_delivery)
    monkeypatch.setattr(core.blocks["grok-routine"], "_post", no_delivery)
    response = client.post("/api/send", headers=owner_headers(client), json={**SEND, "to": target, "dry_run": True})
    assert response.status_code == 200, response.text
    assert response.json()["to"] == resolved
    assert response.json()["dry_run"] is True and response.json()["mailbox"] is True
    assert "handle" not in response.json() and "conversation" not in response.json()
    assert not core.inbox.handles(owner=mailbox) and core.audit.recent() == []


def test_send_preview_never_calls_an_action_that_has_not_opted_in(board, monkeypatch):
    core, client = board

    async def unpreviewable(*args, **kwargs):
        pytest.fail("dry-run invoked a handler with no preview support")

    capy = core.blocks["capy"]
    monkeypatch.setattr(capy, "actions", lambda: [Action("delegate", "", "", unpreviewable)])
    response = client.post("/api/send", headers=owner_headers(client), json={**SEND, "to": "capy", "dry_run": True})
    assert response.status_code == 400 and response.json() == {"error": "send_refused"}


def test_write_errors_never_echo_request_or_upstream_values(board):
    _, client = board
    headers = owner_headers(client)
    denied = [
        ("/api/routes", {**ROUTE, "target": {"captain": SLACK_SECRET}}, "route_not_saved"),
        ("/api/routes", {**ROUTE, "token": SLACK_SECRET}, "invalid_request"),
        ("/api/visibility", {**VISIBILITY, "resource": SLACK_SECRET}, "visibility_not_saved"),
        ("/api/send", {**SEND, "to": SLACK_SECRET}, "send_refused"),
        ("/api/send", {**SEND, "dry_run": "false"}, "invalid_request"),
    ]
    for path, body, error in denied:
        response = client.post(path, headers=headers, json=body)
        assert response.status_code == 400 and response.json() == {"error": error}
        assert SLACK_SECRET not in response.text
