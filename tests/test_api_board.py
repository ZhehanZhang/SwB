import json
import re
from functools import partial

from conftest import CAPTAIN, CAPTAIN2, CAPY_KEY, OWNER, PUBLIC, STATIC_KEY, base_config, default_env
from test_api_read import get


def test_board_routes_require_the_owner(make):
    _, client = make()
    for path in ("/api/board", "/api/board/projects/test"):
        for headers in ({}, {"Authorization": f"Bearer {STATIC_KEY}"}):
            response = client.get(path, headers=headers)
            assert response.status_code == 401
            assert response.json() == {"error": "signed_out"}
            assert response.headers["cache-control"] == "no-store"


def test_board_uses_project_scoped_placements_and_real_counts(make, capy):
    private = "private-fixture-data"
    capy.captains.append({"id": "jam_UNPATCHED", "name": "Another Captain"})
    config = base_config(version=2, turn_cap=8, blocks={
        "mcp": {}, "capy": {},
        "slack": {
            "workspaces": {"team": {"team_id": "T0123ABCD", "intake": "poll", "watched": ["#dev", "#general"],
                                    "identities": {"bot": {"token_env": "FAKE_SLACK_TOKEN"}}}},
            "channel_projects": {"team": {"#dev": "context"}},
        },
        "github": {
            "sinks": {"testing": {"url": f"https://example.test/{private}"}},
            "routes": {"owner/one": ["testing"], "owner/three": ["testing"], "other/*": ["testing"]},
        },
    })
    core, client = make(config, env=default_env())
    project = core.projects.create(slug="context", name="Context", links={
        "repos": ["OWNER/ONE", "owner/two", "other/four"], "claude_project": 'Research "Q4"',
    })
    core.projects.create(slug="other", name="Other")
    core.projects.create(slug="old", name="Old")
    core.projects.update("old", archived=True)
    route = core.routes.set(destination="capy", agent="codex", project="context", target={"captain": CAPTAIN, "token": private},
                            defaults={"secret": private}, allow_override=True, note=private, by="admin:owner@example.test")
    core.routes.set(destination="capy", agent=None, project=None, target={"captain": CAPTAIN2},
                    defaults=None, allow_override=True, note="", by="config")

    core.memory.write(source="codex", scope=project.scope, namespace="notes", title="Build", key="build", body=private)
    deleted = core.memory.write(source="codex", scope=project.scope, namespace="notes", title="Deleted", key="deleted", body=private)
    core.memory.delete(source="codex", id=deleted["id"])
    core.memory.write(source="codex", scope="project:other", namespace="notes", title="Unrelated", key="unrelated", body=private)
    handle = core.inbox.open_handle(owner="codex", destination="capy", ref="jam_THREADEXAMPLE", project=project.slug,
                                    title="Build the UI", meta={"token": private}, target=f"capy:captain:{CAPTAIN}")
    closed = core.inbox.open_handle(owner="codex", destination="capy", ref="jam_CLOSED", project=project.slug,
                                    title="Closed conversation", meta={})
    core.inbox.update_handle(closed, open=False)
    for _ in range(7):
        core.inbox.open_handle(owner="codex", destination="mailbox", ref="gemini", project="other", title=None, meta={})

    connection = core.connections.mint(agent="claude-code", project=project.slug, client="Laptop", by="owner")
    key = core.connections.redeem(connection["code"])
    core.connections.redeem(core.connections.mint(agent="gemini", project=None, client=None, by="owner")["code"])
    revoked = core.connections.redeem(core.connections.mint(agent="grok", project=project.slug, client=None, by="owner")["code"])
    core.connections.revoke(key_id=revoked["key_id"])
    for action, target, ref in (
        ("route_set", "codex/context", None), ("route_remove", "*/other", None),
        ("connect_redeem", key["key_id"], None), ("reply", None, handle),
    ):
        core.audit.record(origin="mcp", source="codex", client=private, destination="capy", action=action, outcome="ok",
                          target=target, ref=ref, excerpt=private, title=private, detail=private)

    board = get(client, "/board")
    assert board["runtime"]["mcp_url"] == f"{PUBLIC}/mcp"
    assert board["runtime"]["online"] and board["runtime"]["host"] and board["runtime"]["version"]
    assert board["runtime"]["uptime_seconds"] >= 0
    assert [p["slug"] for p in board["projects"]] == ["context", "other"]
    [context, other] = board["projects"]
    assert (context["turn_cap"], context["memory_count"], context["open_count"]) == (8, 1, 1)
    assert (other["memory_count"], other["open_count"]) == (1, 7)
    assert [(row["label"], row["kind"]) for row in context["sources"]] == [("#dev", "slack"), ("2 repos", "github")]
    assert other["sources"] == [] and other["agents"] == []
    unassigned = {row["label"] for row in board["unassigned"]}
    assert unassigned >= {"#general", "owner/three", "Gemini", "Another Captain"}
    assert "Acme Captain" not in unassigned
    assert [(row["label"], row["role"], row["by"]["label"]) for row in context["agents"]] == [
        ("Captain", "captain", "Owner"), ("Claude Code", "MCP client", "Owner"),
    ]

    details = get(client, "/board/projects/context")
    assert details["claude_project"] == 'Research "Q4"'
    [placement] = details["routes"]
    assert placement["id"] == str(route.id) and placement["from"]["label"] == "Codex"
    assert placement["to"]["label"] == "Captain" and placement["by"]["label"] == "Owner"
    assert [item["handle"] for item in details["conversations"]] == [handle]
    assert [item["action"] for item in details["history"]] == ["reply", "connect_redeem", "route_set"]
    assert [item["key"] for item in details["memory"]] == ["build"]
    assert get(client, "/board/projects/old")["project"]["archived"]
    assert get(client, "/board/projects/not-present", status=404) == {"error": "project not found"}
    combined = json.dumps((board, details))
    assert all(value not in combined for value in (private, CAPY_KEY, STATIC_KEY, key["key"], connection["code"], "owner@example.test"))


def test_project_listen_and_reply_paths_require_real_wiring(make):
    config = base_config(version=2, blocks={
        "mcp": {}, "claude-routine": {},
        "slack": {
            "workspaces": {"team": {"team_id": "T0123ABCD", "intake": "poll", "watched": ["#eng"],
                                    "identities": {"bot": {"token_env": "FAKE_SLACK_TOKEN"}}}},
            "channel_projects": {"team": {"#listen": "context", "#eng": "context"}},
            "listen_channels": {"team": ["#listen", "#unmapped"]},
        },
    })
    core, client = make(config)
    core.projects.create(slug="context", name="Context")
    token = "sk-ant-oat01-" + "s" * 35
    client.portal.call(partial(core.blocks["claude-routine"].add, fire="trig_CONTEXT", token=token, project="context",
                               name="claude-context", agent="claude", actor="owner", title="Linked Claude Project"))
    target = "claude-routine:routine:claude-context"
    handle = core.inbox.open_handle(owner="slack", destination="claude-routine", ref="trig_CONTEXT", project="context",
                                    title="Reply to Slack", meta={}, target=target)
    core.slack.store.add_relay(handle, "team", "C0123ENG", "100.02", "claude-context")

    data = get(client, "/board/projects/context")
    assert data["claude_project"] == "Linked Claude Project"
    assert [(r["from"]["label"], r["to"]["label"], r["direction"], r["delivery"]) for r in data["routes"]] == [
        ("#listen", "claude-context", "route", "Slack listen mode"),
        ("claude-context", "C0123ENG", "reply", "Slack thread reply"),
    ]
    assert token not in json.dumps(data)


def test_no_mcp_runtime_advertises_no_url(make):
    _, client = make(base_config(version=2, blocks={}))
    runtime = get(client, "/board")["runtime"]
    assert runtime["mcp_url"] is None and not runtime["online"]
    assert re.fullmatch(r"\d+\.\d+\.\d+.*", runtime["version"])
