import base64
import json

from conftest import CAPTAIN, PROJECT, STATIC_KEY, Clock, base_config, call, payload, sign_in


def last(capy, method, path_end):
    return [r for r in capy.requests if r[0] == method and r[1].endswith(path_end)][-1]


def test_delegate_to_captain_by_default(make, capy):
    bridge, c = make()
    tok = sign_in(c)["access_token"]
    out = payload(call(c, tok, "capy_delegate", {"task": "Add a status check for capy-bridge", "repos": ["example-org/status-service"]}))
    assert out["thread_id"] == CAPTAIN and out["delegations_today"] == "1/3"
    _, _, body = last(capy, "POST", f"threads/{CAPTAIN}/message")
    assert body["text"].startswith("[Delegated via capy-bridge from claude]") and "Repos: example-org/status-service" in body["text"]
    assert body["delivery"] == "queue" and len(body["clientKey"]) <= 64
    row = bridge.recent_delegations()[0]
    assert row["source"] == "claude" and row["outcome"] == "ok" and row["client"].startswith("cb_") and row["at"]


def test_delegate_to_project_with_files_and_crew(make, capy):
    bridge, c = make()
    files = [{"name": "spec.md", "text": "# spec"}, {"name": "a.png", "base64": base64.b64encode(b"PNG").decode(), "media_type": "image/png"}]
    out = payload(call(c, STATIC_KEY, "capy_delegate", {"task": "Build it", "target": "infra", "title": "Build", "files": files, "crew": True}))
    tid = out["thread_id"]
    _, _, create = last(capy, "POST", "/threads")
    assert create["projectId"] == PROJECT and create["captainId"] == CAPTAIN and create["title"] == "Build"
    assert [u[1] for u in capy.uploads] == [b"# spec", b"PNG"]
    _, _, follow = last(capy, "POST", f"threads/{tid}/message")
    assert [f["fileId"] for f in follow["files"]] == ["file_spec.md", "file_a.png"]


def test_allowlist_refuses_unknown_targets_and_repos(make, capy):
    bridge, c = make()
    r = call(c, STATIC_KEY, "capy_delegate", {"task": "x", "target": "acme"})
    assert r["isError"] and "not allowed" in r["content"][0]["text"]
    r = call(c, STATIC_KEY, "capy_delegate", {"task": "x", "target": "infra", "repos": ["example-org/site"]})
    assert r["isError"] and "allowlist" in r["content"][0]["text"]
    assert not [x for x in capy.requests if x[0] == "POST"]
    assert [d["outcome"] for d in bridge.recent_delegations()] == ["refused", "refused"]


def test_daily_cap_resets_next_day(make):
    clock = Clock()
    bridge, c = make(clock=clock)
    for i in range(3):
        assert not call(c, STATIC_KEY, "capy_delegate", {"task": f"task {i}"}).get("isError")
    r = call(c, STATIC_KEY, "capy_delegate", {"task": "one more"})
    assert r["isError"] and "daily delegation cap reached (3/3" in r["content"][0]["text"]
    clock.t += 86400
    assert not call(c, STATIC_KEY, "capy_delegate", {"task": "next day"}).get("isError")


def test_status_result_and_prs(make, capy):
    _, c = make()
    tid = payload(call(c, STATIC_KEY, "capy_delegate", {"task": "Do it", "target": "infra"}))["thread_id"]
    capy.threads[tid]["status"] = "idle"
    capy.messages[tid] += [
        {"id": "t1", "source": "tool", "text": "Running tests", "createdAt": "x"},
        {"id": "a1", "source": "assistant", "text": "PR is up: https://github.com/example-org/status-service/pull/12", "createdAt": "x",
         "attachments": [{"file": {"fileId": "f1", "name": "shot.png", "mediaType": "image/png", "sizeBytes": 10}, "title": "Screenshot"}]},
    ]
    st = payload(call(c, STATIC_KEY, "capy_status", {"thread_id": tid}))
    assert st["status"] == "idle" and st["pull_requests"] == ["https://github.com/example-org/status-service/pull/12"]
    assert all(m["source"] != "tool" for m in st["latest"])
    res = payload(call(c, STATIC_KEY, "capy_result", {"thread_id": tid, "tail": 5}))
    assert res["final_reply"]["text"].startswith("PR is up")
    assert res["attachments"][0]["url"].startswith("https://files.example/f1") and res["attachments"][0]["title"] == "Screenshot"
    assert len(res["transcript_tail"]) == 2


def test_tail_falls_back_to_forward_paging(make, capy):
    capy.backward_supported = False
    _, c = make()
    capy.messages[CAPTAIN] = [{"id": str(i), "source": "assistant", "text": f"m{i}", "createdAt": "x"} for i in range(7)]
    res = payload(call(c, STATIC_KEY, "capy_result", {"thread_id": CAPTAIN}))
    assert res["final_reply"]["text"] == "m6"


def test_reply_and_thread_scope(make, capy):
    bridge, c = make()
    r = payload(call(c, STATIC_KEY, "capy_reply", {"thread_id": CAPTAIN, "text": "Go with option A"}))
    assert r["delivery"] == "steer"
    _, _, body = last(capy, "POST", f"threads/{CAPTAIN}/message")
    assert body["text"].startswith("[Reply via capy-bridge from capy]") and body["delivery"] == "steer"
    denied = call(c, STATIC_KEY, "capy_reply", {"thread_id": "jam_OTHER", "text": "hi"})
    assert denied["isError"] and "outside" in denied["content"][0]["text"]
    assert call(c, STATIC_KEY, "capy_status", {"thread_id": "jam_OTHER"})["isError"]
    assert bridge.recent_delegations()[0]["kind"] == "reply"


def test_list_scopes(make):
    _, c = make()
    call(c, STATIC_KEY, "capy_delegate", {"task": "Do it", "target": "infra", "title": "Doing"})
    d = payload(call(c, STATIC_KEY, "capy_list", {}))
    assert d["items"][0]["title"] == "Doing" and d["items"][0]["status"] == "working"
    crew = payload(call(c, STATIC_KEY, "capy_list", {"scope": "captain"}))
    assert {t["title"] for t in crew["items"]} == {"slackgate", "needs owner"}
    proj = payload(call(c, STATIC_KEY, "capy_list", {"scope": "project", "project": "infra"}))
    assert proj["project"] == "infra"


def test_captain_digest(make):
    _, c = make()
    d = payload(call(c, STATIC_KEY, "capy_captain_digest", {}))
    assert d["file"] == "NOTES.md" and "Captain notes" in d["markdown"]
    assert [t["ask"] for t in d["crew_needs_you"]] == ["Merge zhehanzhang/x#1"]
    assert payload(call(c, STATIC_KEY, "capy_captain_digest", {"captain": "captain"}))["captain_id"] == CAPTAIN


def test_context_pack(make):
    _, c = make()
    call(c, STATIC_KEY, "memory_write", {"namespace": "profile", "title": "Timezone", "body": "America/New_York"})
    call(c, STATIC_KEY, "memory_write", {"namespace": "decisions", "title": "Python stack", "body": "Match slackgate"})
    call(c, STATIC_KEY, "memory_write", {"namespace": "projects/capy-bridge", "title": "Port", "body": "8870"})
    call(c, STATIC_KEY, "capy_delegate", {"task": "Do it", "target": "infra", "title": "Doing"})
    text = payload(call(c, STATIC_KEY, "context_pack", {"project": "capy-bridge"}))
    for needle in ("## Profile", "Timezone", "## Recent decisions", "Python stack", "## Project capy-bridge", "8870", "## Active Capy work", "Doing", "## Open asks", "Merge zhehanzhang/x#1"):
        assert needle in text, needle


def test_degraded_without_api_key(make):
    _, c = make(env={"CAPY_BRIDGE_KEY_CAPY": STATIC_KEY})
    h = c.get("/healthz").json()
    assert h["status"] == "degraded" and any("CAPY_API_KEY" in p for p in h["problems"])
    r = call(c, STATIC_KEY, "capy_delegate", {"task": "x"})
    assert r["isError"] and "CAPY_API_KEY" in r["content"][0]["text"]
    assert not call(c, STATIC_KEY, "memory_write", {"namespace": "notes", "title": "t", "body": "b"}).get("isError")
    assert "Offload is not configured" in payload(call(c, STATIC_KEY, "context_pack", {}))
    page = c.get("/")
    assert page.status_code == 200 and "Capy Bridge" in page.text and "CAPY_API_KEY" in page.text


def test_api_key_never_appears_in_logs_or_health(make, caplog):
    from conftest import CAPY_KEY

    _, c = make()
    caplog.set_level("DEBUG")
    call(c, STATIC_KEY, "capy_delegate", {"task": "x"})
    assert CAPY_KEY not in caplog.text and CAPY_KEY not in json.dumps(c.get("/healthz").json())
