import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from importlib.metadata import version

import httpx
import pytest
import uvicorn
from starlette.datastructures import Headers

from conftest import OWNER, PUBLIC, STATIC_KEY, base_config, call, default_env, error_text, new_core, payload, sign_in
from switchboard.__main__ import _NoQuery
from switchboard.app import create_app
from switchboard.hook_wake import FAIL_LIMIT, HOOK_ID, POLL_DEFAULT_S, SCRIPT_TIMEOUT_S, STATE_LIMIT, WORKER_PROMPT

MUSE_KEY = "m" * 32
SECRET = "Launch code TOPSECRET-4471 for the board"
SUBJECT = "Quarterly plan TOPSECRET-SUBJECT"


def env() -> dict[str, str]:
    return {**default_env(), "SWITCHBOARD_KEY_MUSE": MUSE_KEY}


def config(**over):
    return base_config(version=2, blocks={"mcp": {}}, **over)


@pytest.fixture
def hooked(make):
    core, client = make(config=config(), env=env())
    out = payload(call(client, MUSE_KEY, "muse_hook_setup"))
    url = next(line for line in out["script"].splitlines() if line.startswith("URL="))[5:-1]
    return core, client, out, url.removeprefix(PUBLIC)


def send_to_muse(client, text=SECRET, title=SUBJECT):
    return payload(call(client, STATIC_KEY, "send", {"to": "agent:muse", "task": text, "title": title}))


def test_setup_returns_hook_and_script_with_a_hashed_capability(hooked):
    core, client, out, path = hooked
    token = path.split("/")[3]
    assert path == f"/hooks/muse/{token}/state" and token.startswith("swbh_") and len(token) == 48
    assert out["hook"] == {"id": HOOK_ID, "script_path": f"~/hooks/scripts/{HOOK_ID}.sh", "prompt": WORKER_PROMPT,
                           "poll_interval_secs": POLL_DEFAULT_S, "script_timeout_secs": SCRIPT_TIMEOUT_S, "delivery": "main"}
    assert out["script"].count(token) == 1 and "--max-time 4" in out["script"]
    assert len(out["next"]) == 4 and out["next"][0].startswith("Write script to ")
    rows = [dict(r) for r in core.store.q("SELECT * FROM hook_wakes")]
    assert len(rows) == 1 and rows[0]["agent"] == "muse" and token not in json.dumps(rows)
    dump = "\n".join(core.store.db.iterdump())
    assert token not in dump


def test_setup_needs_a_public_https_address(make):
    _, client = make(config=config(public_url="http://localhost:8000"), env=env())
    assert "public HTTPS address" in error_text(call(client, MUSE_KEY, "muse_hook_setup"))
    _, client = make(config=config(public_url="https://swb.example.test/x'y"), env=env())
    assert "public HTTPS address" in error_text(call(client, MUSE_KEY, "muse_hook_setup"))


def test_poll_interval_is_bounded(make):
    _, client = make(config=config(), env=env())
    assert payload(call(client, MUSE_KEY, "muse_hook_setup", {"poll_interval_secs": 30}))["hook"]["poll_interval_secs"] == 30
    for bad in (4, 31):
        assert call(client, MUSE_KEY, "muse_hook_setup", {"poll_interval_secs": bad}).get("isError")


def test_state_returns_metadata_only(hooked):
    core, client, _, path = hooked
    first = client.get(path)
    assert first.status_code == 200 and first.json() == {"pending": 0, "cursor": 0}
    send_to_muse(client)
    got = client.get(path)
    assert got.status_code == 200
    body = got.json()
    assert set(body) == {"pending", "cursor"} and body["pending"] == 1 and body["cursor"] > 0
    assert len(got.content) < 64
    exposed = got.text + json.dumps(dict(got.headers))
    for word in ("TOPSECRET", "Launch", "Quarterly", "capy", "claude", "subject", "text", "from"):
        assert word not in exposed
    assert got.headers["cache-control"] == "no-store" and got.headers["etag"] == f'"{body["cursor"]}.1"'


def test_unknown_capabilities_get_one_uniform_answer(hooked):
    core, client, _, path = hooked
    token = path.split("/")[3]
    answers = []
    for bad in (token[:-1] + ("A" if token[-1] != "A" else "B"), "swbh_" + "x" * 43, "short", token + "x", "swbh_" + "%2F" * 10):
        r = client.get(f"/hooks/muse/{bad}/state")
        answers.append((r.status_code, r.content, r.headers.get("content-type"), r.headers.get("cache-control")))
    assert len(set(answers)) == 1 and answers[0][:2] == (404, b'{"error":"not_found"}')
    assert client.get(path + "x").status_code == 404
    assert client.post(path).status_code in (404, 405)
    refusals = core.store.q("SELECT detail, target FROM audit WHERE action = 'hook_wake'")
    assert refusals and all(token not in json.dumps(dict(r)) for r in refusals)


def test_etag_gives_304_until_something_changes(hooked):
    core, client, _, path = hooked
    etag = client.get(path).headers["etag"]
    for sent in (etag, "W/" + etag, f'"9.9", {etag}'):
        again = client.get(path, headers={"If-None-Match": sent})
        assert again.status_code == 304 and again.content == b"" and again.headers["etag"] == etag
    send_to_muse(client)
    changed = client.get(path, headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.json()["pending"] == 1 and changed.headers["etag"] != etag


def test_unchanged_polls_skip_the_database(hooked, monkeypatch):
    core, client, _, path = hooked
    etag = client.get(path).headers["etag"]
    queries = []
    for name in ("q", "x", "n", "tx"):
        original = getattr(core.store, name)
        monkeypatch.setattr(core.store, name, lambda *a, _o=original, _n=name, **k: queries.append(_n) or _o(*a, **k))
    for _ in range(20):
        assert client.get(path, headers={"If-None-Match": etag}).status_code == 304
    assert queries == []
    monkeypatch.undo()
    send_to_muse(client)
    original = core.store.q
    monkeypatch.setattr(core.store, "q", lambda *a, **k: queries.append("q") or original(*a, **k))
    assert client.get(path, headers={"If-None-Match": etag}).status_code == 200
    assert len(queries) == 1
    assert client.get(path).status_code == 200 and len(queries) == 1


def test_peek_keeps_messages_pending_until_the_worker_acks(hooked):
    core, client, _, path = hooked
    send_to_muse(client)
    send_to_muse(client, "second message")
    for _ in range(2):
        items = payload(call(client, MUSE_KEY, "inbox", {"peek": True}))["items"]
        assert len(items) == 2 and SECRET in json.dumps(items)
        assert client.get(path).json()["pending"] == 2
    top = items[-1]["cursor"]
    assert payload(call(client, MUSE_KEY, "inbox_ack", {"cursor": items[0]["cursor"]}))["cursor"] == items[0]["cursor"]
    assert client.get(path).json() == {"pending": 1, "cursor": top}
    assert payload(call(client, MUSE_KEY, "inbox_ack", {"cursor": 10**12}))["cursor"] == core.inbox.head()
    assert client.get(path).json() == {"pending": 0, "cursor": top}
    assert payload(call(client, MUSE_KEY, "inbox", {"peek": True}))["items"] == []
    send_to_muse(client, "third message")
    assert client.get(path).json()["pending"] == 1
    payload(call(client, MUSE_KEY, "inbox"))
    assert client.get(path).json()["pending"] == 0


def test_acks_touch_only_the_callers_own_position(hooked):
    core, client, _, path = hooked
    send_to_muse(client)
    payload(call(client, STATIC_KEY, "inbox_ack", {"cursor": 10**12}))
    assert client.get(path).json()["pending"] == 1
    assert call(client, MUSE_KEY, "inbox_ack", {"cursor": 0}).get("isError")


def test_the_snapshot_refreshes_after_a_minute_without_events(hooked):
    core, client, _, path = hooked
    assert client.get(path).json()["pending"] == 0
    core.store.x("INSERT INTO inbox(owner, handle, destination, kind, data, at) VALUES ('muse', 'h_x', 'mailbox', 'message', '{}', 0)")
    assert client.get(path).json()["pending"] == 0
    core.clock.t += 61
    assert client.get(path).json()["pending"] == 1


def test_rotation_and_revocation(hooked):
    core, client, _, path = hooked
    assert client.get(path).status_code == 200
    rotated = payload(call(client, MUSE_KEY, "muse_hook_setup"))
    new_path = next(line for line in rotated["script"].splitlines() if line.startswith("URL="))[5:-1].removeprefix(PUBLIC)
    assert new_path != path and client.get(path).status_code == 404 and client.get(new_path).status_code == 200
    status = payload(call(client, MUSE_KEY, "muse_hook_status"))
    assert status["configured"] is True and status["last_check"] and status["pending"] == 0
    assert payload(call(client, MUSE_KEY, "muse_hook_revoke")) == {"revoked": True}
    assert client.get(new_path).status_code == 404
    assert payload(call(client, MUSE_KEY, "muse_hook_status")) == {"configured": False}
    assert payload(call(client, MUSE_KEY, "muse_hook_revoke")) == {"revoked": False}
    actions = [r["action"] for r in core.store.q("SELECT action FROM audit WHERE action LIKE 'muse_hook_%' ORDER BY id")]
    assert actions == ["muse_hook_setup", "muse_hook_setup", "muse_hook_revoke", "muse_hook_revoke"]


def test_console_lists_and_revokes_the_hook(hooked):
    core, client, _, path = hooked
    session = client.get("/auth/session", headers=OWNER).json()
    headers = {**OWNER, "Origin": PUBLIC, "X-CSRF-Token": session["csrf"]}
    rows = [row for row in client.get("/api/tokens", headers=OWNER).json()["stored"] if row["id"] == "hook:muse"]
    assert rows == [{"id": "hook:muse", "name": "Muse hook", "label": "muse", "kind": "muse", "used_by": [], "added_by": "muse",
                     "added_at": rows[0]["added_at"], "revoke_id": "hook:muse"}]
    assert path.split("/")[3] not in client.get("/api/tokens", headers=OWNER).text
    assert client.post("/api/pairing/keys/hook:muse/revoke").status_code in (401, 403)
    assert client.get(path).status_code == 200
    response = client.post("/api/pairing/keys/hook:muse/revoke", headers=headers)
    assert response.status_code == 200 and response.json() == {"id": "hook:muse", "revoked": True}
    assert client.get(path).status_code == 404
    assert client.post("/api/pairing/keys/hook:muse/revoke", headers=headers).status_code == 404


def test_hook_test_message_wakes_the_hook(make):
    core, client = make(config=config(), env=env())
    assert "muse_hook_setup first" in error_text(call(client, MUSE_KEY, "muse_hook_test"))
    out = payload(call(client, MUSE_KEY, "muse_hook_setup"))
    path = next(line for line in out["script"].splitlines() if line.startswith("URL="))[5:-1].removeprefix(PUBLIC)
    sent = payload(call(client, MUSE_KEY, "muse_hook_test"))
    assert sent["sent"] is True and client.get(path).json()["pending"] == 1
    items = payload(call(client, MUSE_KEY, "inbox", {"peek": True}))["items"]
    assert len(items) == 1 and "hook test" in items[0]["text"]


def test_muse_signed_in_through_oauth_sets_up_and_acks(make):
    core, client = make(config=config(), env=env())
    token = sign_in(client, redirect="https://agent.meta.ai/api/hatch/oauth/callback")["access_token"]
    out = payload(call(client, token, "muse_hook_setup"))
    path = next(line for line in out["script"].splitlines() if line.startswith("URL="))[5:-1].removeprefix(PUBLIC)
    send_to_muse(client)
    assert client.get(path).json()["pending"] == 1
    items = payload(call(client, token, "inbox", {"peek": True}))["items"]
    payload(call(client, token, "inbox_ack", {"cursor": items[-1]["cursor"]}))
    assert client.get(path).json()["pending"] == 0
    assert core.store.one("SELECT created_by FROM hook_wakes WHERE agent = 'muse'")["created_by"] == "oauth:muse"


def test_capabilities_survive_a_restart(tmp_path, capy, hooked):
    core, client, _, path = hooked
    again = new_core(tmp_path, capy, config=config(), env=env())
    assert again.hook_wake.agent_for(path.split("/")[3]) == "muse"


def test_unknown_capability_rate_limit_is_strict_per_address(hooked):
    core, client, _, path = hooked
    bad = "/hooks/muse/swbh_" + "q" * 43 + "/state"
    codes = [client.get(bad).status_code for _ in range(FAIL_LIMIT[1] + 2)]
    assert codes == [404] * FAIL_LIMIT[1] + [429, 429]
    limited = client.get(bad)
    assert limited.json() == {"error": "slow_down"} and int(limited.headers["retry-after"]) > 0
    assert client.get(path).status_code == 200


def test_state_rate_limit_allows_five_second_polls_with_headroom(hooked):
    core, client, _, path = hooked
    allowed, window = STATE_LIMIT[1:]
    assert allowed >= 2 * window / 5
    codes = [client.get(path).status_code for _ in range(allowed + 1)]
    assert codes[:allowed] == [200] * allowed and codes[-1] == 429


def test_access_logs_redact_the_capability():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4", "GET", "/hooks/muse/swbh_" + "s" * 43 + "/state", "1.1", 200), None)
    _NoQuery().filter(record)
    assert record.args[2] == "/hooks/muse/[redacted]"


# The detector script, run under bash against a live SwB with a fake Muse hook runtime ------------------------------

RUNTIME = r'''
log() { printf '%s\n' "$*" >&2; }
hook_state_get() {
  [ "$#" -eq 0 ] || { log "hook_state_get takes zero arguments"; exit 2; }
  if [ -f "$HATCH_HOOK_STATE_DIR/state" ]; then cat "$HATCH_HOOK_STATE_DIR/state"; fi
}
hook_state_set() {
  [ "$#" -eq 1 ] || { log "hook_state_set takes one argument"; exit 2; }
  [ "$HATCH_HOOK_DRY_RUN" = 1 ] && return 0
  local tmp
  tmp=$(mktemp "$HATCH_HOOK_STATE_DIR/.state.XXXXXX") || return 1
  printf '%s' "$1" > "$tmp" && mv "$tmp" "$HATCH_HOOK_STATE_DIR/state"
}
disable_after_run() { touch "$HATCH_HOOK_STATE_DIR/.disabled"; }
_result() { jq -cn --arg action "$1" --arg reason "$2" --argjson payload "$3" '{action: $action, reason: $reason, payload: $payload}' | sed 's/^/HATCH_HOOK_RESULT:/'; }
silent() { _result silent "$1" "$2"; }
wake() { _result wake "$1" "$2"; }
'''


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(tmp_path, capy):
    if not all(shutil.which(tool) for tool in ("bash", "curl", "jq")):
        pytest.skip("bash, curl and jq run the hook script")
    core = new_core(tmp_path, capy, config=config(), env=env())
    port = free_port()
    app = create_app(core)
    user_agents = []

    async def capture(scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/hooks/muse/"):
            user_agents.append(Headers(scope=scope).get("user-agent"))
        await app(scope, receive, send)

    server = uvicorn.Server(uvicorn.Config(capture, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{base}/healthz")
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    client = httpx.Client(base_url=base, headers={"Host": "switchboard.example.test"}, timeout=10)
    home = tmp_path / "home"
    (home / "hooks/runtime").mkdir(parents=True)
    (home / "hooks/runtime/hatch_hook_runtime.sh").write_text(RUNTIME)
    state = tmp_path / "state"
    state.mkdir()
    out = payload(call(client, MUSE_KEY, "muse_hook_setup"))
    script = home / "hooks/scripts/swb-wake.sh"
    script.parent.mkdir(parents=True)
    script.write_text(out["script"].replace(PUBLIC, base))
    script.chmod(0o700)

    def run(dry: bool = False) -> dict:
        result = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=SCRIPT_TIMEOUT_S,
                                env={**os.environ, "HOME": str(home), "HATCH_HOOK_ID": HOOK_ID, "HATCH_HOOK_STATE_DIR": str(state),
                                     "HATCH_HOOK_INVOCATION_ID": "test", "HATCH_HOOK_DRY_RUN": "1" if dry else "0"})
        lines = result.stdout.splitlines()
        assert result.returncode == 0 and len(lines) == 1 and lines[0].startswith("HATCH_HOOK_RESULT:"), (result.stdout, result.stderr)
        return json.loads(lines[0].removeprefix("HATCH_HOOK_RESULT:"))

    def saved() -> dict:
        return json.loads((state / "state").read_text())

    def edit(**values) -> None:
        (state / "state").write_text(json.dumps({**saved(), **values}))

    yield {"core": core, "client": client, "run": run, "saved": saved, "edit": edit, "state": state, "server": server,
           "user_agents": user_agents}
    client.close()
    server.should_exit = True
    thread.join(timeout=10)


def test_script_dry_run_reports_and_keeps_no_state(live):
    result = live["run"](dry=True)
    assert result["action"] == "silent" and result["payload"]["event"] == "idle" and result["payload"]["http"] == 200
    assert list(live["state"].iterdir()) == []
    assert live["user_agents"] == [f"SwB/{version('swb-one')} (muse-hook)"]


def test_script_wakes_once_per_batch_and_rewakes_when_unacked(live):
    run, client = live["run"], live["client"]
    first, second = run()["payload"], run()["payload"]
    assert (first["event"], first["http"], second["event"], second["http"]) == ("idle", 200, "idle", 304)
    send_to_muse(client)
    send_to_muse(client, "second message")
    woke = run()
    assert woke["action"] == "wake" and woke["payload"]["event"] == "messages" and woke["payload"]["pending"] == 2
    assert woke["payload"]["repeat"] is False and "TOPSECRET" not in json.dumps(woke)
    assert run()["action"] == "silent"
    live["edit"](reported_at=0)
    again = run()
    assert again["action"] == "wake" and again["payload"]["repeat"] is True
    items = payload(call(client, MUSE_KEY, "inbox", {"peek": True}))["items"]
    payload(call(client, MUSE_KEY, "inbox_ack", {"cursor": items[-1]["cursor"]}))
    assert run()["payload"]["event"] == "idle"
    send_to_muse(client, "third message")
    assert run()["payload"]["pending"] == 1


def test_script_turns_itself_off_after_revocation(live):
    run = live["run"]
    assert run()["action"] == "silent"
    payload(call(live["client"], MUSE_KEY, "muse_hook_revoke"))
    assert [run()["action"] for _ in range(2)] == ["silent", "silent"]
    assert not (live["state"] / ".disabled").exists()
    last = run()
    assert last["action"] == "wake" and last["payload"] == {"event": "rejected", "http": 404}
    assert (live["state"] / ".disabled").exists()


def test_script_reports_a_stale_listener_once(live):
    run = live["run"]
    assert run()["action"] == "silent"
    live["server"].should_exit = True
    time.sleep(1)
    assert run()["payload"]["event"] == "error"
    live["edit"](last_ok=0)
    stale = run()
    assert stale["action"] == "wake" and stale["payload"]["event"] == "stale" and stale["payload"]["since"] == 0
    assert run()["action"] == "silent"
