import pytest

from conftest import CAPTAIN, CAPTAIN2, OTHER_PROJECT, PROJECT, STATIC_KEY, SECOND_KEY, Clock, base_config, call, error_text, payload, sign_in, beacon


def mcp_project(c, tok, **args):
    return payload(call(c, tok, "project_create", args))


# Auto-link and auto-route -------------------------------------------------------------------

def test_exact_repo_match_links_and_routes_to_the_only_captain(make):
    core, c = make()
    tok = sign_in(c)["access_token"]
    out = mcp_project(c, tok, slug="helperbot", name="HelperBot", links={"repos": ["sample-team/orbit"]})
    auto = out["auto"]["results"][0]
    assert [ch.get("link") for ch in auto["changed"] if "link" in ch] == ["capy_project"]
    assert core.projects.get("helperbot").links["capy_project"] == OTHER_PROJECT
    route = core.routes.resolve(destination="capy", agent="claude", project="helperbot")[0]
    assert route.target["captain"] == CAPTAIN2 and route.defaults == {"mode": "captain_thread", "repos": ["sample-team/orbit"]} and route.updated_by == "auto"
    actions = {(r["action"], r["source"]) for r in core.audit.recent(destination="capy", limit=10)}
    assert {("auto_link", "auto"), ("auto_route", "auto")} <= actions


def test_repo_match_beats_name_match(make, capy):
    core, c = make()
    capy.projects.append({"id": "project_NAMED", "name": "Beacon", "code": "ZN", "repos": []})
    capy.projects[1]["name"] = "Something else"
    core.projects.create(slug="beacon", name="Beacon", links={"repos": ["sample-team/orbit"]})
    core_out = payload(call(c, STATIC_KEY, "project_sync", {"slug": "beacon"}))
    assert core_out["changed"] >= 1 and core.projects.get("beacon").links["capy_project"] == OTHER_PROJECT


def test_ambiguous_match_returns_candidates_and_changes_nothing(make, capy):
    core, c = make()
    capy.projects.append({"id": "project_TWIN", "name": "Twin", "code": "TW", "repos": [{"repoFullName": "sample-team/orbit", "baseBranch": "main", "mode": "primary"}]})
    tok = sign_in(c)["access_token"]
    out = mcp_project(c, tok, slug="orbit", name="Orbit", links={"repos": ["sample-team/orbit"]})
    auto = out["auto"]["results"][0]
    assert auto["changed"] == [] and {x["id"] for x in auto["candidates"]["capy_project"]} == {OTHER_PROJECT, "project_TWIN"}
    assert "capy_project" not in core.projects.get("orbit").links
    assert core.routes.resolve(destination="capy", agent="claude", project="orbit")[0].level == "global"


def test_no_captain_leaves_the_global_route(make, capy):
    core, c = make()
    capy.captains = [x for x in capy.captains if x["projectId"] != OTHER_PROJECT]
    out = payload(call(c, STATIC_KEY, "project_create", {"slug": "orbit", "name": "Orbit", "links": {"repos": ["sample-team/orbit"]}}))
    auto = out["auto"]["results"][0]
    assert core.projects.get("orbit").links["capy_project"] == OTHER_PROJECT
    assert auto["candidates"]["captain"] == [] and any("no Captain" in n for n in auto["notes"])
    assert core.routes.resolve(destination="capy", agent="capy", project="orbit")[0].level == "global"


def test_existing_route_and_link_are_left_alone_unless_forced(make):
    core, c = make()
    core.projects.create(slug="orbit", name="Orbit", links={"repos": ["sample-team/orbit"], "capy_project": PROJECT})
    core.routes.set(destination="capy", agent=None, project="orbit", target={"captain": CAPTAIN, "name": "Captain"}, defaults={}, allow_override=False, note="", by="owner")
    out = payload(call(c, STATIC_KEY, "project_sync", {}))
    orbit = [r for r in out["results"] if r["project"] == "orbit"][0]
    assert orbit["changed"] == [] and any("already exists" in n for n in orbit["notes"])
    assert core.projects.get("orbit").links["capy_project"] == PROJECT
    assert core.routes.resolve(destination="capy", agent="x", project="orbit")[0].target["captain"] == CAPTAIN
    forced = payload(call(c, STATIC_KEY, "project_sync", {"slug": "orbit", "force": True}))
    assert forced["changed"] == 2 and core.routes.resolve(destination="capy", agent="x", project="orbit")[0].target["captain"] == CAPTAIN2


# Memory safety ----------------------------------------------------------------------------------

def test_general_writes_need_confirmation(make):
    core, c = make()
    beacon(core)
    err = error_text(call(c, STATIC_KEY, "memory_write", {"scope": "general", "namespace": "notes", "title": "t", "body": "b"}))
    assert "confirm_general=true" in err and "project:<slug>" in err
    ok = payload(call(c, STATIC_KEY, "memory_write", {"scope": "general", "namespace": "notes", "title": "t", "body": "b", "confirm_general": True}))
    assert ok["scope"] == "general"


def test_write_rejected_when_scope_and_project_disagree(make):
    core, c = make()
    beacon(core)
    core.projects.create(slug="orbit", name="Orbit")
    err = error_text(call(c, STATIC_KEY, "memory_write", {"scope": "project:orbit", "project": "beacon", "namespace": "notes", "title": "t", "body": "b"}))
    assert "not the project you named" in err
    ok = payload(call(c, STATIC_KEY, "memory_write", {"scope": "project:beacon", "project": "beacon", "namespace": "notes", "title": "t", "body": "b"}))
    assert ok["scope"] == "project:beacon"


def test_move_and_promote_keep_history_and_are_audited(make):
    core, c = make()
    beacon(core)
    core.projects.create(slug="orbit", name="Orbit")
    w = payload(call(c, STATIC_KEY, "memory_write", {"scope": "project:beacon", "namespace": "notes", "key": "port", "title": "Port", "body": "8870"}))
    assert "memory_promote" in error_text(call(c, STATIC_KEY, "memory_move", {"id": w["id"], "to_scope": "general"}))
    moved = payload(call(c, STATIC_KEY, "memory_move", {"id": w["id"], "to_scope": "project:orbit"}))
    assert moved == {"moved": w["id"], "from": "project:beacon", "to": "project:orbit"}
    promoted = payload(call(c, STATIC_KEY, "memory_promote", {"id": w["id"]}))
    assert promoted["to"] == "general"
    e = core.memory.read(id=w["id"])
    assert e["scope"] == "general" and [h["action"] for h in e["history"]][:3] == ["move project:orbit -> general", "move project:beacon -> project:orbit", "create"]
    assert [r["action"] for r in core.audit.recent(destination="memory")] == ["memory_promote", "memory_move"]


def test_cross_project_reads_are_explicit_logged_and_respect_private(make):
    core, c = make()
    beacon(core)
    core.projects.create(slug="orbit", name="Orbit")
    core.projects.create(slug="diary", name="Diary", private=True)
    for slug in ("beacon", "orbit", "diary"):
        call(c, STATIC_KEY, "memory_write", {"scope": f"project:{slug}", "namespace": "notes", "title": f"{slug} deploy", "body": "deploy notes"})
    plain = payload(call(c, STATIC_KEY, "memory_search", {"query": "deploy", "project": "beacon"}))
    assert {i["scope"] for i in plain["items"]} == {"project:beacon"}
    assert core.audit.recent(destination="memory") == []
    wide = payload(call(c, STATIC_KEY, "memory_search", {"query": "deploy", "project": "beacon", "scopes": ["project:beacon", "project:orbit"]}))
    assert [i["scope"] for i in wide["items"]] == ["project:beacon", "project:orbit"]
    row = core.audit.recent(destination="memory")[0]
    assert row["action"] == "cross_read" and row["target"] == "project:orbit"
    assert "private" in error_text(call(c, STATIC_KEY, "memory_search", {"query": "deploy", "project": "beacon", "scopes": ["project:diary"]}))
    own = payload(call(c, STATIC_KEY, "memory_list", {"project": "diary"}))
    assert {i["scope"] for i in own["items"]} == {"project:diary"}
    general = payload(call(c, STATIC_KEY, "memory_search", {"query": "deploy"}))
    assert general["items"] == []


def test_key_scope_allowlist(make):
    cfg = base_config()
    cfg["auth"]["key_scopes"] = {"second": ["project:orbit"]}
    core, c = make(config=cfg)
    beacon(core)
    core.projects.create(slug="orbit", name="Orbit")
    w = payload(call(c, STATIC_KEY, "memory_write", {"scope": "project:beacon", "namespace": "notes", "title": "Z", "body": "beacon only"}))
    assert payload(call(c, SECOND_KEY, "memory_write", {"scope": "project:orbit", "namespace": "notes", "title": "P", "body": "orbit"}))["scope"] == "project:orbit"
    for tool, args in (("memory_write", {"scope": "project:beacon", "namespace": "notes", "title": "x", "body": "y"}),
                       ("memory_read", {"id": w["id"]}),
                       ("memory_delete", {"id": w["id"]}),
                       ("memory_search", {"query": "beacon", "project": "beacon"}),
                       ("context_pack", {"project": "beacon"}),
                       ("routes_set", {"target": "captain:Captain"}),
                       ("project_create", {"slug": "new", "name": "New"}),
                       ("capy_delegate", {"task": "x", "project": "beacon"})):
        assert "limited to project:orbit" in error_text(call(c, SECOND_KEY, tool, args)) or "may not read" in error_text(call(c, SECOND_KEY, tool, args)), tool
    items = payload(call(c, SECOND_KEY, "memory_search", {"query": "orbit", "project": "orbit"}))["items"]
    assert {i["scope"] for i in items} == {"project:orbit"}
    assert payload(call(c, SECOND_KEY, "switchboard_status", {}))["you"]["scopes"] == ["project:orbit"]


# Runaway guards -----------------------------------------------------------------------------------

def test_same_task_to_same_target_returns_the_existing_handle(make, capy):
    clock = Clock()
    core, c = make(clock=clock, config=base_config(destinations={"capy": {"default_route": {"captain": CAPTAIN}, "reconnect_base_s": 0.01}}))
    first = payload(call(c, STATIC_KEY, "capy_delegate", {"task": "Fix the build"}))
    again = payload(call(c, STATIC_KEY, "capy_delegate", {"task": "Fix the build"}))
    assert again["handle"] == first["handle"] and again["duplicate"] is True
    assert len([r for r in capy.requests if r[0] == "POST" and r[1].endswith("/message")]) == 1
    assert [r["outcome"] for r in core.audit.recent(action="delegate")] == ["duplicate", "ok"]
    clock.t += 601
    assert payload(call(c, STATIC_KEY, "capy_delegate", {"task": "Fix the build"}))["handle"] != first["handle"]


def ping_pong(c, turns):
    """capy and second answer each other on one conversation; returns how many replies went through and the last error."""
    h = payload(call(c, STATIC_KEY, "send", {"task": "ping", "to": "agent:second"}))["conversation"]
    keys = [SECOND_KEY, STATIC_KEY]
    for i in range(turns):
        out = call(c, keys[i % 2], "reply", {"conversation": h, "text": "ack" if i % 2 else "ok"})
        if out.get("isError"):
            return i, error_text(out)
        h = payload(out)["conversation"]
    return turns, None


def test_turn_cap_pauses_a_looping_conversation_only(make):
    core, c = make(config={**base_config(), "turn_cap": 10})
    done, err = ping_pong(c, 30)
    assert done == 9 and "is paused" in err and "10 turns" in err
    assert core.audit.recent(action="turn_cap")[0]["outcome"] == "refused"
    assert core.health()["turn_cap"] == {"per_conversation": 10, "paused": 1}
    done, err = ping_pong(c, 5)
    assert done == 5 and err is None
    for _ in range(12):
        assert not call(c, STATIC_KEY, "send", {"task": "fresh work", "to": "agent:second"}).get("isError")


def test_a_busy_burst_of_sends_and_replies_passes(make):
    core, c = make(config=base_config(destinations={"capy": {"default_route": {"captain": CAPTAIN}, "reconnect_base_s": 0.01}}))
    done, err = ping_pong(c, 60)
    assert (done, err) == (60, None)
    for i in range(25):
        assert not call(c, STATIC_KEY, "capy_delegate", {"task": f"task {i}"}).get("isError")
    assert core.health()["turn_cap"] == {"per_conversation": 200, "paused": 0}


def test_delegation_is_not_described_as_scarce(make):
    from conftest import rpc
    _, c = make(config=base_config(destinations={"capy": {"default_route": {"captain": CAPTAIN}}}))
    tools = {t["name"]: t for t in rpc(c, STATIC_KEY, "tools/list").json()["result"]["tools"]}
    d = tools["capy_delegate"]["description"]
    assert "per day" not in d.lower() and "cheap" in d and "capy_reply" in d
    out = payload(call(c, STATIC_KEY, "capy_delegate", {"task": "x"}))
    assert "used_today" not in out
