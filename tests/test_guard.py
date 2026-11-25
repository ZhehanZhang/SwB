from switchboard.guard import RateLimiter
from conftest import CLAUDE_CB, STATIC_KEY, rpc


def test_failed_bearer_keys_are_audited_and_locked_out(make):
    core, c = make()
    for _ in range(20):
        assert rpc(c, "wrong-key-" + "x" * 30, "tools/list").status_code == 401
    assert rpc(c, "wrong-key-" + "x" * 30, "tools/list").status_code == 429
    locked = rpc(c, STATIC_KEY, "tools/list")
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0
    fails = core.audit.recent(action="auth_failure", limit=50)
    assert len(fails) == 20 and fails[0]["target"] == "/mcp" and fails[0]["outcome"] == "refused"


def test_missing_token_is_not_counted(make):
    core, c = make()
    for _ in range(30):
        assert rpc(c, None, "tools/list").status_code == 401
    assert rpc(c, STATIC_KEY, "tools/list").status_code == 200
    assert core.audit.recent(action="auth_failure") == []


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
