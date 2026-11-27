import hashlib
from urllib.parse import urlsplit

from conftest import CAPY_KEY, OWNER, PUBLIC, SERVICE_KEY, STATIC_KEY, Clock, base_config, default_env
from switchboard.claims import CLAIM_TTL_S


def _csrf(page):
    return page.text.split('name="csrf" value="')[1].split('"')[0]


def _register(block, fire, title):
    return block.register(fire=fire, claude_project=title, project="context", project_name=None, description=None,
                          create_new=False, name=None, primary=None, actor="oauth:claude-context")["link"]


def test_waiting_only_contains_live_claim_metadata(make):
    clock = Clock()
    core, client = make(config=base_config(version=2, blocks={"mcp": {}, "claude-routine": {}}), clock=clock)
    core.projects.create(slug="context", name="Context")
    routine = core.blocks["claude-routine"]
    expired = core.claims.mint("EXPIRED_TOKEN", PUBLIC)
    clock.t += CLAIM_TTL_S
    pending = core.claims.mint("EXAMPLE_WEBHOOK", PUBLIC, url=True)
    superseded = _register(routine, "trig_SUPERSEDE", "Primary")
    active = _register(routine, "trig_ACTIVE", "Primary")
    stored = _register(routine, "trig_USED", "Other")
    routine_token = "sk-ant-" + "q" * 30
    routine.complete(stored.rsplit("/", 1)[-1], routine_token, actor="admin:owner@example.test")

    for headers in ({}, {"authorization": f"Bearer {STATIC_KEY}"}, {"cf-access-jwt-assertion": "other"}):
        denied = client.get("/api/tokens", headers=headers)
        assert denied.status_code == 401 and denied.json() == {"error": "signed_out"}
    response = client.get("/api/tokens", headers=OWNER)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    inventory = response.json()
    waiting = {row["label"]: row for row in inventory["waiting"]}
    assert set(waiting) == {"EXAMPLE_WEBHOOK", "claude-context"}
    assert waiting["EXAMPLE_WEBHOOK"]["name"] == "Webhook URL"
    assert waiting["EXAMPLE_WEBHOOK"]["asked_by"] is None
    assert waiting["claude-context"]["asked_by"] == "claude-context"
    assert waiting["claude-context"]["used_by"] == ["context"]
    assert all(row["requested_at"] < row["expires_at"] for row in waiting.values())
    [saved] = inventory["stored"]
    assert saved["name"] == "Claude routine token" and saved["added_by"] == "you" and saved["revoke_id"] is None
    for link in (expired, pending, superseded, active, stored):
        code = link.rsplit("/", 1)[-1]
        assert code not in response.text and hashlib.sha256(code.encode()).hexdigest() not in response.text
    for value in ("trig_ACTIVE", "trig_SUPERSEDE", "owner@example.test", routine_token):
        assert value not in response.text
    clock.t += CLAIM_TTL_S
    assert client.get("/api/tokens", headers=OWNER).json()["waiting"] == []


def test_stored_inventory_never_reads_or_renders_plaintext(make, monkeypatch):
    configured_url = "https://hooks.example.test/opaque-secret?key=private"
    secret = "this-is-a-stored-example-token"
    cfg = base_config(version=2, blocks={"mcp": {}, "capy": {}, "github": {
        "sinks": {"example": {"url": configured_url}},
    }})
    core, client = make(config=cfg)
    path = urlsplit(core.claims.mint("EXAMPLE_TOKEN", PUBLIC)).path
    response = client.post(path, headers=OWNER, data={"token": secret, "csrf": _csrf(client.get(path, headers=OWNER))})
    assert response.status_code == 200
    rotation = core.claims.mint("EXAMPLE_TOKEN", PUBLIC)

    def no_decrypt(_name):
        raise AssertionError("the inventory must not decrypt credentials")

    monkeypatch.setattr(core.claims, "secret", no_decrypt)
    response = client.get("/api/tokens", headers=OWNER)
    data = response.json()
    assert {r["label"] for r in data["stored"]} >= {"CAPY_API_KEY", "CAPY_API_KEY_SERVICE", "EXAMPLE_TOKEN"}
    assert [r["label"] for r in data["waiting"]] == ["EXAMPLE_TOKEN"]
    assert all(r["revoke_id"] is None for r in data["stored"])
    assert next(r for r in data["stored"] if r["label"] == "EXAMPLE_TOKEN")["added_by"] == "you"
    sealed = core.store.one("SELECT sealed FROM secrets WHERE name = 'EXAMPLE_TOKEN'")["sealed"].decode()
    for value in (secret, sealed, configured_url, rotation, CAPY_KEY, SERVICE_KEY, STATIC_KEY):
        assert value not in response.text


def test_only_owner_can_revoke_a_paired_key(make):
    core, client = make()
    pairing = core.connections.mint(agent="claude-context", project="context", client=None, by="admin:owner@example.test")
    key = core.connections.redeem(pairing["code"])
    path = f"/api/pairing/keys/{key['key_id']}/revoke"
    response = client.get("/api/tokens", headers=OWNER)
    row = next(row for row in response.json()["stored"] if row["id"] == key["key_id"])
    assert row["revoke_id"] == key["key_id"] and row["used_by"] == ["context"]
    assert key["key"] not in response.text and pairing["code"] not in response.text

    csrf = client.get("/auth/session", headers=OWNER).json()["csrf"]
    verified = {**OWNER, "origin": PUBLIC, "x-csrf-token": csrf}
    assert client.post(path, headers={"authorization": f"Bearer {key['key']}"}).status_code == 401
    assert client.post(path, headers=OWNER).status_code == 403
    assert client.post(path, headers={**verified, "origin": "https://other.example.test"}).status_code == 403
    assert client.post("/api/pairing/keys/EXAMPLE_TOKEN/revoke", headers=verified).status_code == 404
    assert core.connections.verify(key["key"]) is not None
    assert client.post(path, headers=verified).json() == {"id": key["key_id"], "revoked": True}
    assert core.connections.verify(key["key"]) is None
    assert client.post(path, headers=verified).status_code == 404
    assert key["key_id"] not in client.get("/api/tokens", headers=OWNER).text
    assert core.audit.recent(action="connect_revoke", outcome="ok")[0]["target"] == key["key_id"]


def test_chat_credential_kinds_come_from_configured_blocks(make):
    token = "fake-env-value-must-not-be-disclosed"
    cfg = base_config(version=2, blocks={
        "release-bot": {"use": "discord", "connect": False, "token_env": "RELEASE_TOKEN"},
        "ops-bot": {"use": "telegram", "connect": False, "token_env": "OPS_TOKEN"},
    })
    core, client = make(config=cfg, env={**default_env(), "RELEASE_TOKEN": token})
    core.claims.mint("OPS_TOKEN", PUBLIC)
    core.claims.mint("DISCORD_UNRELATED", PUBLIC)

    response = client.get("/api/tokens", headers=OWNER)
    assert token not in response.text
    data = response.json()
    assert [(row["label"], row["kind"]) for row in data["stored"]] == [("RELEASE_TOKEN", "discord")]
    assert {row["label"]: row["kind"] for row in data["waiting"]} == {
        "OPS_TOKEN": "telegram", "DISCORD_UNRELATED": "generic",
    }


def test_standalone_handoff_uses_only_local_accent_assets_and_a_password_field(make):
    core, client = make(env=default_env())
    path = urlsplit(core.claims.mint("EXAMPLE_TOKEN", PUBLIC)).path
    response = client.get(path, headers=OWNER)
    assert response.status_code == 200
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["cache-control"] == "no-store"
    assert "form-action 'self'" in response.headers["content-security-policy"]
    assert "/assets/claim-brand.svg" in response.text and "/assets/claim.css" in response.text
    assert response.text.count("<script") == 1 and '<script src="/assets/theme.js"></script>' in response.text
    assert "script-src 'self';" in response.headers["content-security-policy"] and "bridge.css" not in response.text
    assert path not in response.text and 'name="token" type="password" required' in response.text
    assert 'method="post"' in response.text
    assert client.get("/assets/claim-brand.svg").headers["content-type"] == "image/svg+xml"
    assert client.get("/assets/claim.css").headers["content-type"].startswith("text/css")
    assert client.get("/assets/theme.js").headers["content-type"].startswith("text/javascript")
