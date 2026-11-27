from urllib.parse import urlsplit

import pytest
from starlette.testclient import TestClient

from conftest import CLAUDE_CB, HEADERS, OWNER, PUBLIC, STATIC_KEY, Clock, rpc, rpc_body
from switchboard.app import create_app
from switchboard.audit import REFUSAL_AUDIT_INTERVAL_S
from switchboard.core import Switchboard
from switchboard.guard import LIMITS, RateLimiter


def test_failed_bearer_keys_are_audited_and_locked_out(make):
    core, c = make()
    for _ in range(20):
        assert rpc(c, "wrong-key-" + "x" * 30, "tools/list").status_code == 401
    assert rpc(c, "wrong-key-" + "x" * 30, "tools/list").status_code == 429
    locked = rpc(c, STATIC_KEY, "tools/list")
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0
    fails = core.audit.recent(action="auth_failure", limit=50)
    assert len(fails) == 20 and fails[0]["target"] == "/mcp" and fails[0]["outcome"] == "refused"


@pytest.mark.parametrize(("proxies", "peer", "headers", "expected"), [
    ("", "192.0.2.1", {"CF-Connecting-IP": "198.51.100.1", "X-Forwarded-For": "198.51.100.2"}, "192.0.2.1"),
    ("192.0.2.0/24", "198.51.100.3", {"CF-Connecting-IP": "198.51.100.1"}, "198.51.100.3"),
    ("192.0.2.0/24", "192.0.2.1", {"CF-Connecting-IP": "2001:0db8::1"}, "2001:db8::1"),
    ("cloudflare", "2400:cb00::1", {"CF-Connecting-IP": "198.51.100.1"}, "198.51.100.1"),
    ("192.0.2.0/24", "192.0.2.1",
     {"CF-Connecting-IP": "198.51.100.1", "X-Forwarded-For": "198.51.100.2, 198.51.100.3, 192.0.2.2"}, "198.51.100.3"),
    ("192.0.2.0/24", "192.0.2.1", [(b"x-forwarded-for", b"198.51.100.1"), (b"x-forwarded-for", b"198.51.100.3, 192.0.2.2")],
     "198.51.100.3"),
    ("192.0.2.0/24", "192.0.2.1", {"X-Forwarded-For": "198.51.100.1, unknown"}, "192.0.2.1"),
    ("192.0.2.0/24", "192.0.2.1", [(b"cf-connecting-ip", b"\xff")], "192.0.2.1"),
])
def test_rate_limits_and_audit_share_the_resolved_proxy_address(tmp_path, proxies, peer, headers, expected):
    core = Switchboard(data_dir=tmp_path, env={"SWB_TRUSTED_PROXIES": proxies})
    app = create_app(core)
    header_items = list(headers.items()) if isinstance(headers, dict) else headers
    auth = [*HEADERS.items(), ("Authorization", "Bearer fixture-wrong-key")]
    with TestClient(app, client=(peer, 10000)) as client:
        response = client.post("/mcp", headers=[*auth, *header_items], content=rpc_body("tools/list", None))
        assert response.status_code == 401
        assert core.audit.recent(action="auth_failure")[0]["client"] == expected
        assert list(app.limiter.hits["bearer_fail"]) == [expected]


@pytest.mark.parametrize(("path", "bucket", "action", "body"), [
    ("/mcp", "bearer_fail", "auth_failure", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
    ("/companion/pair", "companion_pair_fail", "companion_pair", {"code": "WRONG-CODE"}),
])
def test_untrusted_rotating_headers_leave_rate_limits_in_force(tmp_path, path, bucket, action, body):
    core = Switchboard(data_dir=tmp_path, env={})
    app = create_app(core)
    peer = "192.0.2.1"
    with TestClient(app, client=(peer, 10000)) as client:
        limit = LIMITS[bucket][0]
        for index in range(limit + 1):
            headers = {**HEADERS, "Authorization": "Bearer fixture-wrong-key", "CF-Connecting-IP": f"198.51.100.{index + 1}",
                       "X-Forwarded-For": f"203.0.113.{index + 1}"}
            response = client.post(path, headers=headers, json=body)
            assert response.status_code == (401 if index < limit else 429)
        assert int(response.headers["Retry-After"]) > 0
        rows = core.audit.recent(action=action, limit=50)
        assert {row["client"] for row in rows} == {peer}
        if action == "companion_pair":
            assert [row["detail"] for row in rows] == ["Pairing attempt limit reached.", "Pairing code expired or invalid."]
        else:
            assert len(rows) == limit


def test_missing_token_is_not_counted(make):
    core, c = make()
    for _ in range(30):
        assert rpc(c, None, "tools/list").status_code == 401
    assert rpc(c, STATIC_KEY, "tools/list").status_code == 200
    assert core.audit.recent(action="auth_failure") == []


def test_csrf_refusals_are_audited_without_request_values_and_rate_limited(make):
    clock = Clock()
    core, client = make(clock=clock)
    claim = urlsplit(core.claims.mint("EXAMPLE_TOKEN", PUBLIC)).path
    origin = "https://other.example.test"
    for peer in range(20):
        headers = {**OWNER, "cf-connecting-ip": f"192.0.2.{peer + 1}"}
        for path, extra in ((f"/api/fixture-{peer}", {}), (claim, {"origin": origin})):
            response = client.post(path, headers={**headers, **extra}, json={"token": "fixture-value"})
            assert response.status_code == 403 and response.json() == {"error": "csrf"}

    for rows in (core.audit.recent(action="auth_failure"), client.get("/api/audit", headers=OWNER,
                                                                     params={"action": "auth_failure"}).json()["items"]):
        assert [(row["target"], row["detail"], row["outcome"]) for row in rows] == [
            ("/claim", "csrf", "refused"), (None, "csrf", "refused"),
        ]
        assert claim not in str(rows) and origin not in str(rows) and "fixture-value" not in str(rows)

    clock.t += REFUSAL_AUDIT_INTERVAL_S
    assert client.post(claim, headers={**OWNER, "origin": origin}).status_code == 403
    assert len(core.audit.recent(action="auth_failure")) == 3


def test_register_is_rate_limited(make):
    core, c = make()
    codes = [c.post("/register", json={"redirect_uris": [CLAUDE_CB], "client_name": "Claude"}).status_code for _ in range(21)]
    assert codes[:20] == [201] * 20 and codes[20] == 429
    assert core.audit.recent(action="rate_limited")[0]["target"] == "/register"


def test_request_size_limits(make):
    _, c = make()
    assert c.post("/register", content=b"x" * (1024 * 1024 + 1), headers={"content-type": "application/json"}).status_code == 413
    big = rpc(c, STATIC_KEY, "tools/call", {"name": "memory_write", "arguments": {"scope": "general", "confirm_general": True, "namespace": "notes", "title": "t", "body": "y" * (2 * 1024 * 1024)}})
    assert big.status_code == 200


def test_limiter_window_slides():
    now = [0.0]
    lim = RateLimiter(clock=lambda: now[0], limits={"x": (2, 10.0)})
    lim.hit("x", "ip")
    lim.hit("x", "ip")
    assert lim.blocked("x", "ip") == 10.0 and lim.blocked("x", "other") is None
    now[0] = 10.5
    assert lim.blocked("x", "ip") is None


def test_limiter_evicts_the_least_recently_hit_peer_without_resetting_other_buckets():
    lim = RateLimiter(clock=lambda: 0.0, limits={"peer": (1, 10.0), "total": (1, 20.0)},
                      max_keys_per_bucket=2)
    lim.hit("total", "*")
    for peer in ("first", "oldest", "first", "new"):
        lim.hit("peer", peer)
    assert list(lim.hits["peer"]) == ["first", "new"]
    for peer in ("oldest", "unknown"):
        assert lim.blocked("peer", peer) is None
    assert len(lim.hits["peer"]) == 2
    assert lim.blocked("peer", "first") is not None
    assert lim.blocked("total", "*") is not None


def test_limiter_expires_idle_peers_without_looking_them_up():
    now = [0.0]
    lim = RateLimiter(clock=lambda: now[0], limits={"peer": (2, 10.0), "total": (1, 20.0)})
    lim.hit("peer", "idle")
    lim.hit("peer", "active")
    lim.hit("total", "*")
    now[0] = 4.0
    lim.hit("peer", "active")
    now[0] = 10.0
    assert lim.blocked("peer", "unknown") is None
    assert list(lim.hits["peer"]) == ["active"]
    assert lim.blocked("peer", "active") is None
    assert lim.blocked("total", "*") == 10.0


def test_limiter_preserves_expiry_order_when_the_clock_moves_backward():
    now = [1000.0]
    lim = RateLimiter(clock=lambda: now[0], limits={"peer": (1, 600.0)})
    lim.hit("peer", "first")
    now[0] = 999.0
    lim.hit("peer", "second")
    assert lim.blocked("peer", "second") == 600.0
    now[0] = 1599.5
    assert lim.blocked("peer", "first") == lim.blocked("peer", "second") == 1.0
    now[0] = 1600.0
    assert lim.blocked("peer", "second") is None
    assert not lim.hits["peer"]


def test_auth_failures_say_what_was_wrong_without_the_key(make):
    core, c = make()
    bad = '"' + STATIC_KEY[:10] + ' ' + STATIC_KEY[10:]
    assert rpc(c, bad, "tools/list").status_code == 401
    row = core.audit.recent(action="auth_failure")[0]
    assert "34 chars" in row["detail"] and "whitespace" in row["detail"] and "quoted" in row["detail"] and STATIC_KEY not in row["detail"]
    r = c.post("/mcp", headers={"Authorization": "Token abc def", "Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
               json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401 and "not 'Bearer <token>'" in core.audit.recent(action="auth_failure")[0]["detail"]
    for _ in range(3):
        rpc(c, None, "tools/list")
    missing = core.audit.recent(action="auth_missing")
    assert len(missing) == 1 and "no Authorization header" in missing[0]["detail"]


def test_a_bare_key_without_the_bearer_scheme_works(make):
    core, c = make()
    hdr = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert c.post("/mcp", headers={**hdr, "Authorization": STATIC_KEY}, json=body).status_code == 200
    assert c.post("/mcp", headers={**hdr, "Authorization": "  " + STATIC_KEY + " "}, json=body).status_code == 200
    assert c.post("/mcp", headers={**hdr, "Authorization": "s" * 31}, json=body).status_code == 401
    assert core.audit.recent(action="auth_failure")[0]["detail"].startswith("unknown or expired bearer token (31 chars")
