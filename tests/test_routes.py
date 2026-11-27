import pytest

from switchboard.routes import RouteError, Routes
from switchboard.store import Store
from conftest import CAPTAIN, CAPTAIN2, OWNER, PROJECT, SECOND_KEY, STATIC_KEY, Clock, base_config, call, error_text, payload, sign_in, beacon


@pytest.fixture
def routes(tmp_path):
    return Routes(Store(tmp_path / "r.db", clock=Clock()))


def put(routes, agent, project, name):
    routes.set(destination="capy", agent=agent, project=project, target={"captain": name}, defaults={}, allow_override=False, note="", by="t")


def test_resolution_order(routes):
    assert routes.resolve(destination="capy", agent="claude", project="beacon") == (None, ["agent+project", "project", "agent", "global"])
    put(routes, None, None, "global")
    put(routes, "claude", None, "agent")
    put(routes, None, "beacon", "project")
    put(routes, "claude", "beacon", "agent+project")

    def target(agent, project):
        r, _ = routes.resolve(destination="capy", agent=agent, project=project)
        return r.target["captain"], r.level

    assert target("claude", "beacon") == ("agent+project", "agent+project")
    assert target("chatgpt", "beacon") == ("project", "project")
    assert target("claude", "orbit") == ("agent", "agent")
    assert target("claude", None) == ("agent", "agent")
    assert target("chatgpt", None) == ("global", "global")
    routes.remove(destination="capy", agent="claude", project="beacon")
    assert target("claude", "beacon") == ("project", "project")
    with pytest.raises(RouteError):
        routes.remove(destination="capy", agent="claude", project="beacon")


def test_override_needs_permission(make, capy):
    core, c = make()
    beacon(core)
    core.routes.set(destination="capy", agent=None, project="beacon", target={"captain": CAPTAIN}, defaults={}, allow_override=False, note="", by="t")
    task = "fixture-private-route-task"
    err = error_text(call(c, STATIC_KEY, "capy_delegate", {"task": task, "project": "beacon", "target": "captain:Orbit Captain"}))
    assert "does not allow overriding" in err
    [row] = core.audit.recent(outcome="refused")
    assert (row["destination"], row["action"], row["detail"]) == ("capy", "delegate", "Choose a target granted to this key.")
    assert task not in str(row) and STATIC_KEY[:12] not in str(row)
    core.routes.set(destination="capy", agent=None, project="beacon", target={"captain": CAPTAIN}, defaults={}, allow_override=True, note="", by="t")
    out = payload(call(c, STATIC_KEY, "capy_delegate", {"task": "x", "project": "beacon", "target": "captain:Orbit Captain"}))
    assert out["thread_id"] == CAPTAIN2 and out["route"] == "explicit"
    assert "not linked" not in err


@pytest.mark.parametrize("target", ["nonsense:foo", "capy:nonsense:foo"])
def test_unknown_route_kind_names_the_target_and_audits_once(make, target):
    core, client = make()
    before = core.routes.list()
    assert target in error_text(call(client, STATIC_KEY, "routes_set", {"target": target}))
    [row] = core.audit.recent(outcome="refused")
    assert (row["target"], row["detail"]) == ("routes_set", "Choose a supported route target kind.")
    assert target not in str(row) and core.routes.list() == before


def test_no_route_needs_a_target(make):
    cfg = {"public_url": "https://switchboard.example.test", "auth": {"owner_emails": ["owner@example.test"]}, "destinations": {"capy": {}}}
    _, c = make(config=cfg)
    assert "No route is set for Capy" in error_text(call(c, STATIC_KEY, "capy_delegate", {"task": "x"}))
    assert payload(call(c, STATIC_KEY, "capy_delegate", {"task": "x", "target": CAPTAIN}))["route"] == "explicit"


def test_send_checks_for_capy_credentials_before_a_route(make):
    cfg = {"version": 2, "blocks": {"mcp": {}, "capy": {}}}
    core, client = make(config=cfg, env={"SWITCHBOARD_KEY_CAPY": STATIC_KEY})
    args = {"to": "capy", "task": "Check the build"}
    expected = "Capy is unreachable. Run swb connect capy-captain to add an API key."
    assert expected in error_text(call(client, STATIC_KEY, "send", args))
    put(core.routes, None, None, CAPTAIN)
    assert expected in error_text(call(client, STATIC_KEY, "send", args))


def test_route_tools_validate_and_audit(make):
    core, c = make()
    beacon(core)
    tok = sign_in(c)["access_token"]
    r = payload(call(c, tok, "routes_set", {"target": "captain:Orbit Captain", "project": "beacon", "agent": "claude", "defaults": {"repos": ["example-org/web-app"]}}))
    assert r["target"] == {"captain": CAPTAIN2, "name": "Orbit Captain", "captain_project": "project_OTHER"} and r["level"] == "agent+project"
    assert "no Captain matches" in error_text(call(c, tok, "routes_set", {"target": "captain:Nobody"}))
    assert "no project" in error_text(call(c, tok, "routes_set", {"target": "captain:Captain", "project": "nope"}))
    assert payload(call(c, tok, "routes_set", {"target": f"project:BCN"}))["target"]["project"] == PROJECT
    explained = payload(call(c, tok, "routes_resolve", {"project": "beacon"}))
    assert explained["agent"] == "claude" and explained["route"]["level"] == "agent+project" and explained["checked"] == ["agent+project"]
    assert len(payload(call(c, tok, "routes_list"))["routes"]) == 2
    payload(call(c, tok, "routes_remove", {"project": "beacon", "agent": "claude"}))
    log = [(a["action"], a["outcome"], a["source"]) for a in core.audit.recent(destination="capy") if a["action"].startswith("route")]
    assert log == [("route_remove", "ok", "claude"), ("route_set", "ok", "claude"), ("route_set", "refused", "claude"), ("route_set", "ok", "claude")]


def test_seeded_default_route_is_global_and_written_once(make):
    core, _ = make()
    (r,) = core.routes.list()
    assert r.level == "global" and r.target == {"captain": CAPTAIN} and r.updated_by == "config"


def test_project_rename_moves_routes(make):
    core, _ = make()
    beacon(core)
    core.routes.set(destination="capy", agent=None, project="beacon", target={"captain": CAPTAIN}, defaults={}, allow_override=False, note="", by="t")
    core.projects.update("beacon", new_slug="infra")
    assert core.routes.resolve(destination="capy", agent="x", project="infra")[0].level == "project"


def test_routes_set_matches_a_captain_by_code(make):
    core, c = make()
    beacon(core)
    tok = sign_in(c)["access_token"]
    assert payload(call(c, tok, "routes_set", {"target": "captain:orbit-3", "project": "beacon"}))["target"]["captain"] == CAPTAIN2
    assert payload(call(c, tok, "routes_set", {"target": "captain:BCN-1"}))["target"]["captain"] == CAPTAIN


def test_mailbox_routes_send_and_show_up_with_only_mcp_enabled(make):
    core, client = make(config=base_config(version=2, blocks={"mcp": {}}))
    core.projects.create(slug="demo", name="Demo")
    args = {"project": "demo"}
    assert set(core.blocks) == {"mcp"}
    assert "unknown" in error_text(call(client, STATIC_KEY, "routes_set", {**args, "target": "agent:unknown"}))
    route = payload(call(client, STATIC_KEY, "routes_set", {**args, "target": "agent:second"}))
    assert route["destination"] == "agent" and route["level"] == "project"
    assert payload(call(client, STATIC_KEY, "routes_list"))["routes"] == [route]
    resolved = payload(call(client, STATIC_KEY, "routes_resolve", args))
    assert resolved["to"] == "agent:second" and resolved["route"] == route
    assert payload(call(client, STATIC_KEY, "routes_resolve", {**args, "block": "agent"})) == resolved
    out = payload(call(client, STATIC_KEY, "send", {**args, "task": "Check the build"}))
    assert out["to"] == "agent:second" and out["mailbox"]
    (item,) = payload(call(client, SECOND_KEY, "inbox", args))["items"]
    assert item["handle"] == out["conversation"] and item["text"] == "Check the build"
    (placement,) = client.get("/api/board/projects/demo", headers=OWNER).json()["routes"]
    assert placement["to"]["id"] == "agent:second" and placement["state"] == "on"
    assert payload(call(client, STATIC_KEY, "routes_remove", args))["removed"] == route
    assert core.audit.recent(action="route_remove")[0]["destination"] == "agent"
    assert "No route is set" in error_text(call(client, STATIC_KEY, "send", {**args, "task": "Check again"}))
