import base64
import json

from conftest import CAPTAIN, CAPY_KEY, OWNER, PROJECT, STATIC_KEY, Clock, base_config, call, error_text, payload, sign_in, beacon


def last(capy, method, path_end):
    return [r for r in capy.requests if r[0] == method and r[1].endswith(path_end)][-1]


def test_delegate_follows_the_global_route_to_the_captain(make, capy):
    core, c = make()
    beacon(core)
    tok = sign_in(c)["access_token"]
    out = payload(call(c, tok, "capy_delegate", {"task": "Add a status check for switchboard", "project": "beacon", "repos": ["example-org/status-service"]}))
    assert out["thread_id"] == CAPTAIN and out["route"] == "global" and "used_today" not in out and out["handle"].startswith("h_")
    _, _, body = last(capy, "POST", f"threads/{CAPTAIN}/message")
    assert body["text"].startswith("Add a status check for switchboard\n") and "switchboard" not in body["text"].split("\n")[0][:12] and "Repos: example-org/status-service" in body["text"]
    assert "switchboard: done" not in body["text"] and out["handle"] not in body["text"] and body["delivery"] == "queue" and len(body["clientKey"]) <= 64
    row = core.audit.recent(action="delegate")[0]
    assert row["source"] == "claude" and row["outcome"] == "ok" and row["ref"] == CAPTAIN and row["origin"] == "mcp"
    h = core.inbox.handle(out["handle"])
    assert h["open"] and h["owner"] == "claude" and h["project"] == "beacon" and h["stream_cursor"] == "01A"


def test_project_route_starts_a_thread_with_files_and_model(make, capy):
    core, c = make()
    beacon(core)
    core.routes.set(destination="capy", agent=None, project="beacon", target={"project": PROJECT, "name": "Beacon"},
                    defaults={"model": "anthropic/claude-opus-5-5"}, allow_override=False, note="", by="test")
    files = [{"name": "spec.md", "text": "# spec"}, {"name": "a.png", "base64": base64.b64encode(b"PNG").decode(), "media_type": "image/png"}]
    out = payload(call(c, STATIC_KEY, "capy_delegate", {"task": "Build it", "project": "beacon", "title": "Build", "files": files}))
    assert out["route"] == "project" and out["mode"] == "new_thread"
    _, _, create = last(capy, "POST", "/threads")
    assert create["projectId"] == PROJECT and "captainId" not in create and create["title"] == "Build" and create["model"] == {"modelId": "anthropic/claude-opus-5-5"}
    assert [u[1] for u in capy.uploads] == [b"# spec", b"PNG"]
    _, _, follow = last(capy, "POST", f"threads/{out['thread_id']}/message")
    assert [f["fileId"] for f in follow["files"]] == ["file_spec.md", "file_a.png"]


def test_captain_route_can_start_crew_threads(make, capy):
    core, c = make()
    payload(call(c, STATIC_KEY, "routes_set", {"target": "captain:Captain", "defaults": {"mode": "new_thread"}}))
    out = payload(call(c, STATIC_KEY, "capy_delegate", {"task": "Do it"}))
    _, _, create = last(capy, "POST", "/threads")
    assert out["mode"] == "new_thread" and create["captainId"] == CAPTAIN and create["projectId"] == PROJECT


def test_repo_allowlist_comes_from_project_links(make, capy):
    core, c = make()
    beacon(core)
    assert "not linked" in error_text(call(c, STATIC_KEY, "capy_delegate", {"task": "x", "project": "beacon", "repos": ["example-org/unlinked-repo"]}))
    assert "no project" in error_text(call(c, STATIC_KEY, "capy_delegate", {"task": "x", "project": "orbit"}))
    assert not [x for x in capy.requests if x[0] == "POST" and not x[1].endswith("/send-now")]
    assert [d["outcome"] for d in core.audit.recent(action="delegate")] == ["refused", "refused"]


def test_optional_daily_cap_resets_next_day(make):
    clock = Clock()
    _, c = make(clock=clock)
    for i in range(3):
        assert not call(c, STATIC_KEY, "capy_delegate", {"task": f"task {i}"}).get("isError")
    assert "daily capy delegate cap reached (3/3" in error_text(call(c, STATIC_KEY, "capy_delegate", {"task": "one more"}))
    clock.t += 86400
    assert not call(c, STATIC_KEY, "capy_delegate", {"task": "next day"}).get("isError")


def test_status_result_and_prs_by_handle(make, capy):
    core, c = make()
    beacon(core)
    core.routes.set(destination="capy", agent=None, project="beacon", target={"project": PROJECT}, defaults={}, allow_override=False, note="", by="t")
    out = payload(call(c, STATIC_KEY, "capy_delegate", {"task": "Do it", "project": "beacon"}))
    tid = out["thread_id"]
    capy.threads[tid]["status"] = "idle"
    capy.messages[tid] += [
        {"id": "t1", "source": "tool", "text": "Running tests", "createdAt": "x"},
        {"id": "a1", "source": "assistant", "text": "PR is up: https://github.com/example-org/status-service/pull/12", "createdAt": "x",
         "attachments": [{"file": {"fileId": "f1", "name": "shot.png", "mediaType": "image/png", "sizeBytes": 10}, "title": "Screenshot"}]},
    ]
    st = payload(call(c, STATIC_KEY, "capy_status", {"thread_id": out["handle"]}))
    assert st["status"] == "idle" and st["pull_requests"] == ["https://github.com/example-org/status-service/pull/12"]
    assert all(m["source"] != "tool" for m in st["latest"])
    res = payload(call(c, STATIC_KEY, "capy_result", {"thread_id": tid, "tail": 5}))
    assert res["final_reply"]["text"].startswith("PR is up")
    assert res["attachments"][0]["url"].startswith("https://files.example/f1") and res["attachments"][0]["title"] == "Screenshot"


def test_tail_falls_back_to_forward_paging(make, capy):
    capy.backward_supported = False
    _, c = make()
    capy.messages[CAPTAIN] = [{"id": str(i), "source": "assistant", "text": f"m{i}", "createdAt": "x"} for i in range(7)]
    assert payload(call(c, STATIC_KEY, "capy_result", {"thread_id": CAPTAIN}))["final_reply"]["text"] == "m6"


def test_reply_and_thread_scope(make, capy):
    core, c = make()
    r = payload(call(c, STATIC_KEY, "capy_reply", {"thread_id": CAPTAIN, "text": "Go with option A"}))
    assert r["delivery"] == "steer"
    _, _, body = last(capy, "POST", f"threads/{CAPTAIN}/message")
    assert body["text"] == "Go with option A" and body["delivery"] == "steer"
    assert "outside" in error_text(call(c, STATIC_KEY, "capy_reply", {"thread_id": "jam_OTHER", "text": "hi"}))
    assert call(c, STATIC_KEY, "capy_status", {"thread_id": "jam_OTHER"})["isError"]
    assert [a["outcome"] for a in core.audit.recent(action="reply")] == ["refused", "ok"]


def test_list_scopes_and_captains(make):
    core, c = make()
    beacon(core)
    call(c, STATIC_KEY, "capy_delegate", {"task": "Do it", "title": "Doing"})
    d = payload(call(c, STATIC_KEY, "capy_list", {}))
    assert d["items"][0]["task"] == "Doing" and d["items"][0]["following"] is True
    crew = payload(call(c, STATIC_KEY, "capy_list", {"scope": "captain"}))
    assert {t["title"] for t in crew["items"]} == {"Release check", "needs owner", "other project work"}
    assert payload(call(c, STATIC_KEY, "capy_list", {"scope": "project", "project": "beacon"}))["project"] == "beacon"
    caps = payload(call(c, STATIC_KEY, "capy_captains"))["captains"]
    assert [(x["name"], x["routed"]) for x in caps] == [("Captain", True), ("Orbit Captain", False)]


def test_captain_digest(make):
    _, c = make()
    d = payload(call(c, STATIC_KEY, "capy_captain_digest", {}))
    assert d["file"] == "NOTES.md" and CAPTAIN in d["markdown"]
    assert [t["ask"] for t in d["crew_needs_you"]] == ["Merge example-org/web-app#1"]


def test_context_pack_includes_project_memory_and_the_routed_captain(make):
    from conftest import CAPTAIN2

    core, c = make()
    beacon(core)
    core.routes.set(destination="capy", agent=None, project="beacon", target={"captain": CAPTAIN2, "name": "Orbit Captain"}, defaults={}, allow_override=False, note="", by="t")
    call(c, STATIC_KEY, "memory_write", {"scope": "general", "confirm_general": True, "namespace": "profile", "title": "Timezone", "body": "America/Chicago"})
    call(c, STATIC_KEY, "memory_write", {"scope": "general", "confirm_general": True, "namespace": "decisions", "title": "Python stack", "body": "Use the shared runtime"})
    call(c, STATIC_KEY, "memory_write", {"scope": "project:beacon", "namespace": "notes", "title": "Port", "body": "8870"})
    call(c, STATIC_KEY, "capy_delegate", {"task": "Do it", "project": "beacon", "title": "Doing"})
    text = payload(call(c, STATIC_KEY, "context_pack", {"project": "beacon"}))
    for needle in ("Project Beacon", "8870", "General: profile", "Timezone", "Python stack", "## Capy", "captain Orbit Captain", "project route",
                   "Delegations in progress", "Doing", f"Captain notes for {CAPTAIN2}", "needs you: Merge example-org/web-app#1", "working: Release check"):
        assert needle in text, needle
    assert "other project work" not in text
    general = payload(call(c, STATIC_KEY, "context_pack", {}))
    assert "8870" not in general and f"Captain notes for {CAPTAIN}" in general


def test_degraded_without_api_key(make):
    _, c = make(env={"SWITCHBOARD_KEY_CAPY": STATIC_KEY})
    h = c.get("/healthz", headers=OWNER).json()
    assert h["status"] == "degraded" and [p for p in h["problems"] if "CAPY_API_KEY" in p] and h["destinations"]["capy"]["online"] is False
    assert "swb connect capy-captain" in error_text(call(c, STATIC_KEY, "capy_delegate", {"task": "x"}))
    assert not call(c, STATIC_KEY, "memory_write", {"scope": "general", "confirm_general": True, "namespace": "notes", "title": "t", "body": "b"}).get("isError")
    assert "CAPY_API_KEY missing" in payload(call(c, STATIC_KEY, "context_pack", {}))
    health = c.get("/api/health", headers=OWNER)
    assert health.status_code == 200 and health.json()["status"] == "degraded"
    assert health.json()["blocks"]["capy"]["online"] is False
    assert "capy: check block status" in health.json()["problems"]


def test_api_key_never_appears_in_logs_or_health(make, caplog):
    _, c = make()
    caplog.set_level("DEBUG")
    call(c, STATIC_KEY, "capy_delegate", {"task": "x"})
    assert CAPY_KEY not in caplog.text and CAPY_KEY not in json.dumps(c.get("/healthz", headers=OWNER).json())


def test_sends_go_as_the_service_user_and_reads_as_the_main_key(make, capy):
    from conftest import SERVICE_KEY, last_auth
    core, c = make()
    out = payload(call(c, STATIC_KEY, "capy_delegate", {"task": "Ship it"}))
    health = c.get("/healthz", headers=OWNER).json()["destinations"]["capy"]
    assert health["sends_as"] == "CAPY_API_KEY_SERVICE" and health["identity"] == "service" and health["sends_as_agents"]["claude"] == "CAPY_API_KEY_SERVICE"
    assert last_auth(capy, "POST", f"threads/{CAPTAIN}/message") == SERVICE_KEY
    assert last_auth(capy, "GET", f"threads/{CAPTAIN}/messages") == CAPY_KEY
    call(c, STATIC_KEY, "capy_reply", {"thread_id": out["handle"], "text": "and docs"})
    rows = core.audit.recent(destination="capy", limit=5)
    assert {(r["action"], r["detail"]) for r in rows if r["action"] in ("delegate", "reply")} == {("delegate", "sent as CAPY_API_KEY_SERVICE"), ("reply", "sent as CAPY_API_KEY_SERVICE")}


def test_without_a_service_key_sends_are_refused_never_sent_as_the_owner(make, capy):
    from conftest import default_env
    env = {k: v for k, v in default_env().items() if k != "CAPY_API_KEY_SERVICE"}
    core, c = make(env=env)
    assert "CAPY_API_KEY_SERVICE" in " ".join(c.get("/healthz", headers=OWNER).json()["problems"])
    assert "never sends as CAPY_API_KEY" in error_text(call(c, STATIC_KEY, "capy_delegate", {"task": "Ship it"}))
    assert not [r for r in capy.requests if r[0] == "POST" and r[1].endswith("/message")]
    assert core.audit.recent(action="delegate")[0]["outcome"] != "ok"


def test_identity_personal_opts_in_to_sending_as_the_main_key(make, capy):
    from conftest import default_env, last_auth
    env = {k: v for k, v in default_env().items() if k != "CAPY_API_KEY_SERVICE"}
    cfg = base_config(destinations={"capy": {"default_route": {"captain": CAPTAIN}, "reconnect_base_s": 0.01, "identity": "personal"}})
    core, c = make(config=cfg, env=env)
    call(c, STATIC_KEY, "capy_delegate", {"task": "Ship it"})
    assert last_auth(capy, "POST", f"threads/{CAPTAIN}/message") == CAPY_KEY
    assert core.audit.recent(action="delegate")[0]["detail"] == "sent as CAPY_API_KEY" and not c.get("/healthz", headers=OWNER).json()["problems"]


def test_an_agent_key_sends_for_that_agent_and_falls_back_to_the_service_user_only(make, capy):
    from conftest import SERVICE_KEY, default_env, last_auth
    claude_key, cc_key = "capy_as_claude_" + "z" * 30, "capy_as_cc_" + "q" * 30
    capy.keys |= {claude_key, cc_key}
    core, c = make(env={**default_env(), "CAPY_API_KEY_AS_CLAUDE": claude_key, "CAPY_API_KEY_AS_CLAUDE_CODE": cc_key})
    tok = sign_in(c)["access_token"]
    health = c.get("/healthz", headers=OWNER).json()["destinations"]["capy"]
    assert health["sends_as_agents"]["claude"] == "CAPY_API_KEY_AS_CLAUDE" and health["sends_as_agents"]["capy"] == "CAPY_API_KEY_SERVICE"
    assert health["sends_as_agents"]["claude-code"] == "CAPY_API_KEY_AS_CLAUDE_CODE"
    out = payload(call(c, tok, "capy_delegate", {"task": "Ship it"}))
    assert last_auth(capy, "POST", f"threads/{CAPTAIN}/message") == claude_key
    capy.refused_keys.add(claude_key)
    call(c, tok, "capy_reply", {"thread_id": out["handle"], "text": "and docs"})
    assert last_auth(capy, "POST", f"threads/{CAPTAIN}/message") == SERVICE_KEY
    assert core.audit.recent(action="reply")[0]["detail"] == "sent as CAPY_API_KEY_SERVICE: CAPY_API_KEY_AS_CLAUDE was refused (403)"
    fb = core.audit.recent(action="send_key_fallback")
    assert len(fb) == 1 and fb[0]["source"] == "claude" and fb[0]["target"] == "CAPY_API_KEY_AS_CLAUDE"
    capy.refused_keys.add(SERVICE_KEY)
    n = len([r for r in capy.auths if r[2] == CAPY_KEY and r[0] == "POST"])
    assert "every send key was refused" in error_text(call(c, tok, "capy_reply", {"thread_id": out["handle"], "text": "third"}))
    assert len([r for r in capy.auths if r[2] == CAPY_KEY and r[0] == "POST"]) == n
    assert core.audit.recent(action="reply")[0]["outcome"] != "ok"
    assert core.audit.recent(action="send_refused")[0]["detail"] == "not sent: CAPY_API_KEY_AS_CLAUDE was refused (403), CAPY_API_KEY_SERVICE was refused (403)"


async def test_key_checks_report_project_and_device_access(amake, capy):
    from conftest import SERVICE_KEY, default_env, eventually
    a, b = "capy_as_gemini_" + "g" * 30, "capy_as_slack_" + "s" * 30
    capy.keys |= {a, b}
    capy.hidden_from[a] = {CAPTAIN}
    capy.device_denied.add(b)
    core, c = await amake(env={**default_env(), "CAPY_API_KEY_AS_GEMINI": a, "CAPY_API_KEY_AS_SLACK": b})
    dest = core.destination("capy")
    await eventually(lambda: len(dest.key_checks) == 4)
    checks = {env: v["status"] for env, v in dest.key_checks.items()}
    assert checks == {"CAPY_API_KEY": "ok", "CAPY_API_KEY_SERVICE": "ok", "CAPY_API_KEY_AS_GEMINI": "no project access", "CAPY_API_KEY_AS_SLACK": "no device access"}
    assert CAPTAIN in dest.key_checks["CAPY_API_KEY_AS_GEMINI"]["detail"] and "DeviceAccessDenied" in dest.key_checks["CAPY_API_KEY_AS_SLACK"]["detail"]
    health = (await c.get("/healthz", headers=OWNER)).json()
    assert health["status"] == "degraded" and health["destinations"]["capy"]["send_keys"]["CAPY_API_KEY_AS_SLACK"]["status"] == "no device access"
    assert SERVICE_KEY not in json.dumps(health) and a not in json.dumps(health)
