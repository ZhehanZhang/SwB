import hashlib
import hmac
import json
from importlib.resources import files

import pytest

from conftest import CAPTAIN, CAPTAIN2, CAPY_KEY, OWNER, PUBLIC, SERVICE_KEY, STATIC_KEY, Clock, base_config, call, default_env, payload

PRIVATE = "fixture-secret-not-owner-console-data"
READ_PATHS = ("/health", "/routes", "/visibility", "/audit", "/agents", "/projects", "/inbox",
              "/conversations/not-found", "/memory", "/prompts", "/prompts/coordinator", "/platforms")


def get(client, path, *, status=200, **params):
    response = client.get(f"/api{path}", headers=OWNER, params=params)
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("application/json")
    return response.json()


@pytest.mark.parametrize("headers", [{}, {"cf-access-jwt-assertion": "other"}, {"Authorization": f"Bearer {STATIC_KEY}"}])
def test_all_reads_require_the_access_owner(make, headers):
    _, client = make()
    for path in READ_PATHS:
        response = client.get(f"/api{path}", headers=headers)
        assert response.status_code == 401 and response.json() == {"error": "signed_out"}, path
        assert response.headers["cache-control"] == "no-store"
    assert client.get("/healthz").status_code == 200


def test_health_platforms_routes_and_visibility_expose_only_their_display_fields(make, monkeypatch):
    config = base_config(version=2, blocks={"mcp": {}, "capy": {"test_only_setting": PRIVATE}},
                         visibility={"resources": {"tool:send": "private", "target:captain": "shared"}})
    core, client = make(config, env={**default_env(), "SWITCHBOARD_REVISION": PRIVATE})
    monkeypatch.setattr(core.blocks["capy"], "problems", lambda: [f"Upstream error: {PRIVATE}"])
    route = core.routes.set(destination="capy", agent="codex", project=None,
                            target={"captain": CAPTAIN, "name": "Captain", "token": PRIVATE},
                            defaults={"mode": "captain_thread", "repos": ["owner/repo", f"https://example.test/{PRIVATE}"], "secret": PRIVATE},
                            allow_override=True, note="route note", by="admin")
    core.visibility.set(f"route:{route.id}", ["U123"], "admin", core.clock())

    health = get(client, "/health")
    assert health["status"] == "degraded" and health["problems"] == ["capy: check block status"]
    assert "commit" not in health
    monkeypatch.setitem(core.cfg.env, "SWITCHBOARD_REVISION", "AB" * 20)
    assert get(client, "/health")["commit"] == "ab" * 20
    assert health["blocks"]["capy"] == {"use": "capy", "kind": "sink", "level": {"out": "L4", "in": "mcp"}, "online": True}
    assert "capy" in health["static_keys"]
    platforms = {r["name"]: r for r in get(client, "/platforms")["platforms"]}
    assert platforms["capy"]["status"] == "degraded" and platforms["capy"]["online"]
    assert "test_only_setting" in platforms["capy"]["config_keys"]
    assert platforms["slack"]["status"] == "unconfigured" and not platforms["slack"]["online"]
    assert set(platforms["capy"]) == {"name", "use", "title", "kind", "config_keys", "status", "online"}
    [row] = get(client, "/routes")["routes"]
    assert row["level"] == "agent" and row["allow_override"] and row["id"] == route.id
    assert row["target"] == {"destination": "capy", "kind": "captain", "id": CAPTAIN, "name": "Captain"}
    assert row["defaults"] == {"mode": "captain_thread", "repos": ["owner/repo"]}
    resources = {r["resource"]: r for r in get(client, "/visibility")["resources"]}
    assert resources["tool:send"]["fixed"]
    assert resources["target:captain"]["source"] == "config" and resources["target:captain"]["visibility"] == "shared"
    assert resources[f"route:{route.id}"]["source"] == "override" and resources[f"route:{route.id}"]["visibility"] == ["U123"]
    assert resources["memory:general"]["source"] == "default"
    output = json.dumps((health, platforms, row, resources))
    assert all(secret not in output for secret in (CAPY_KEY, SERVICE_KEY, STATIC_KEY, PRIVATE))


def test_owner_health_reports_pending_routing_without_showing_the_message(make):
    core, client = make()
    handle, recipient = core.inbox.store_owner_item(source="fixture", project=None, title=PRIVATE, meta={},
                                                   kind="message", data={"text": PRIVATE})
    assert recipient is None
    health = get(client, "/health")
    assert health["status"] == "degraded" and health["problems"] == ["owner_delivery_pending"]
    assert health["owner_delivery"] == {"pending": 1}
    assert PRIVATE not in json.dumps(health)
    assert "owner_delivery" not in client.get("/healthz").json()
    assert not get(client, "/inbox")["conversations"]
    assert get(client, f"/conversations/{handle}", status=404) == {"error": "conversation not found"}


def test_owner_trusted_http_inbox_poll_counts_as_agent_activity(make):
    core, client = make()
    headers = {"Authorization": f"Bearer {STATIC_KEY}"}
    assert client.get("/inbox/poll?agent=other&timeout=0", headers=headers).status_code == 403
    assert core.owner_delivery.resolve(None) is None
    assert client.get("/inbox/poll?timeout=0", headers=headers).status_code == 200
    assert core.owner_delivery.resolve(None) == ("capy", "active")


def test_audit_filters_and_pages_at_equal_timestamps_without_exposing_raw_diagnostics(make):
    core, client = make()
    for action, origin, outcome in (("route_set", "mcp", "ok"), ("route_remove", "mcp", "ok"), ("connect_code", "mcp", "ok"),
                                    ("route_remove", "http", "ok"), ("auth_failure", "http", "refused")):
        core.audit.record(action=action, origin=origin, outcome=outcome, destination="core", source="codex",
                          client=PRIVATE if action == "auth_failure" else "key:codex", target=f"/inbox/{PRIVATE}" if action == "auth_failure" else "codex/*",
                          ref=PRIVATE if action == "auth_failure" else None, detail=PRIVATE, excerpt=PRIVATE, title=PRIVATE)
    filters = {"actions": "route_set, route_remove,route_set", "origin": "mcp", "outcome": "ok", "limit": "1"}
    first = get(client, "/audit", **filters)
    second = get(client, "/audit", cursor=first["next_cursor"], **filters)
    assert [r["action"] for p in (first, second) for r in p["items"]] == ["route_remove", "route_set"]
    assert first["items"][0]["target"] == "codex/*" and second["next_cursor"] is None
    assert PRIVATE not in json.dumps((first, second))
    [refusal] = get(client, "/audit", action="auth_failure")["items"]
    assert refusal["outcome"] == "refused" and all(refusal[field] is None for field in ("client", "target", "ref"))
    assert PRIVATE not in json.dumps(refusal)
    assert get(client, "/audit", limit=PRIVATE, status=400) == {"error": "invalid limit"}
    assert get(client, "/audit", cursor="99999999999999999999", status=400) == {"error": "invalid cursor"}


def test_audit_exposes_only_known_dropped_webhook_reasons(make):
    core, client = make()
    for detail in ("channel mode off", "assist target is missing", "triage target is unavailable", PRIVATE):
        core.audit.record(action="webhook", origin="fixture", outcome="dropped", destination="fixture",
                          source="fixture", client="local", detail=detail, excerpt=PRIVATE)
    rows = get(client, "/audit", action="webhook")["items"]
    assert [row["detail"] for row in rows] == [None, "triage target is unavailable", "assist target is missing", "channel mode off"]
    assert PRIVATE not in json.dumps(rows)


def test_audit_chat_drop_details_are_allowlisted(make):
    core, client = make()
    for detail in ("external bot message", PRIVATE):
        core.audit.record(action="chat_event", origin="fixture", outcome="dropped", destination="fixture",
                          source="fixture", client="", detail=detail, excerpt=PRIVATE)
    rows = get(client, "/audit", action="chat_event")["items"]
    assert [row["detail"] for row in rows] == [None, "external bot message"]
    assert PRIVATE not in json.dumps(rows)


def test_audit_shows_webhook_refusal_and_drop_reasons_from_the_log(make):
    secret = "fixture-secret-for-webhook-signing"
    core, client = make(config=base_config(version=2, blocks={"mcp": {}, "github": {}}),
                        env={**default_env(), "GITHUB_WEBHOOK_SECRET": secret})
    assert client.post("/github/webhook", content=PRIVATE).status_code == 401
    body = b'{"repository":{"full_name":"example/repo"}}'
    signature = "sha256=" + hmac.digest(secret.encode(), body, "sha256").hex()
    response = client.post("/github/webhook", content=body, headers={"X-Hub-Signature-256": signature, "X-GitHub-Event": "fixture"})
    assert response.status_code == 200
    items = get(client, "/audit", action="webhook")["items"]
    cli_rows = core.audit.recent(action="webhook")
    assert [r["detail"] for r in items] == [r["detail"] for r in cli_rows] == ["unhandled event", "missing signature"]
    assert [r["excerpt"] for r in items] == [r["excerpt"] for r in cli_rows] == ["", ""]
    assert all(secret_value not in json.dumps(items) for secret_value in (PRIVATE, secret, signature))


def test_project_and_memory_audit_contains_only_intentional_details(make):
    core, client = make()

    def change(tool, args, key=STATIC_KEY):
        result = call(client, key, tool, args)
        assert not result.get("isError"), result
        return payload(result)

    change("project_create", {"slug": "example", "name": "Example", "description": PRIVATE})
    pairing = change("connect_code", {"agent": "paired", "project": "example"})
    key = client.post("/connect", json={"code": pairing["code"]}).json()["key"]
    change("project_update", {"slug": "example", "name": "New name", "new_slug": "renamed"})
    refused = call(client, key, "project_update", {"slug": "renamed", "description": PRIVATE})
    assert refused["isError"] is True
    [refusal] = get(client, "/audit", action="tool_refused")["items"]
    assert (refusal["source"], refusal["target"], refusal["detail"]) == (
        "paired", "project_update", "Use the owner's sign-in to call this tool.",
    )
    written = change("memory_write", {"scope": "project:renamed", "namespace": "notes", "title": "Test note", "body": PRIVATE}, key)
    change("memory_delete", {"id": written["id"]}, key)
    change("project_update", {"slug": "renamed", "archived": True, "private": True})
    actions = "project_create,project_update,memory_write,memory_delete"
    rows = get(client, "/audit", actions=actions)["items"]
    cli_rows = [r for r in core.audit.recent() if r["action"] in actions.split(",")]
    assert [(r["action"], r["target"], r["detail"]) for r in rows] == [
        (r["action"], r["target"], r["detail"]) for r in cli_rows
    ] == [
        ("project_update", "renamed", "Archived project. Set project to private."),
        ("memory_delete", written["id"], "Deleted memory."),
        ("memory_write", "project:renamed", "Test note"),
        ("project_update", "renamed", "Renamed project."),
        ("project_create", "example", "Created project."),
    ]
    assert rows[2]["metadata"] == {"scope": "project:renamed"}
    assert {r["source"] for r in rows[1:3]} == {"paired"}
    assert all(secret not in json.dumps((rows, cli_rows, refusal)) for secret in (PRIVATE, key, pairing["code"]))


def test_audit_reads_legacy_mailbox_send_rows_with_the_new_action(make):
    core, client = make()
    core.audit.record(origin="mcp", source="second", client="key:second", destination="mailbox",
                      action="agent_send", outcome="ok", target="capy")
    payload(call(client, STATIC_KEY, "send", {"to": "agent:second", "task": "Test mailbox audit"}))
    assert [r["action"] for r in get(client, "/audit", action="send")["items"]] == ["send", "agent_send"]
    assert [r["action"] for r in core.audit.recent(action="send")] == ["send", "agent_send"]
    assert [r["action"] for r in core.audit.recent(action="agent_send")] == ["agent_send"]


def test_agent_inventory_has_discovered_targets_and_only_credential_metadata(make):
    config = base_config(version=2, blocks={"mcp": {}, "capy": {}, "claude-routine": {},
                                           "dots": {"delivery": "mailbox", "dots": {"research": {"name": "Research"}}}})
    routine_token = "sk-ant-oat01-" + "x" * 35
    core, client = make(config, env=default_env())
    core.projects.create(slug="context", name="Context")
    block = core.blocks["claude-routine"]
    block.add(fire="trig_CONFIG", token=routine_token, project="context", name="claude-context", agent="claude", actor="owner")
    pending = block.register(fire="trig_PENDING", claude_project="Claude Garden", project="garden", project_name="Garden",
                             description="Outdoor ideas", create_new=True, name=None, primary=None, actor="oauth:claude")
    one_time_claim = pending["link"].rsplit("/", 1)[-1]
    pairing = core.connections.mint(agent="codex", project="context", client="laptop", by="admin")
    key = core.connections.redeem(pairing["code"])
    assert core.connections.verify(key["key"]) is not None
    rows = get(client, "/agents")
    assert [r["name"] for r in rows["routines"]] == ["claude-context"]
    assert rows["routines"][0]["routine_id"] == "trig_CONFIG" and rows["routines"][0]["primary"]
    assert [r["id"] for r in rows["keys"]] == [key["key_id"]] and rows["keys"][0]["last_used_at"]
    [claim] = rows["pending_claims"]
    assert claim["project"] == "garden" and claim["claude_project"] == "Claude Garden" and claim["expires_at"]
    assert claim["new_project"]["name"] == "Garden"
    assert {r["id"] for r in rows["captains"]} >= {CAPTAIN}
    assert [r["name"] for r in rows["dots"]] == ["Research"]
    assert "capy" in rows["static_keys"]
    output = json.dumps(rows)
    assert all(secret not in output for secret in (
        CAPY_KEY, SERVICE_KEY, STATIC_KEY, routine_token, key["key"], pairing["code"], one_time_claim,
        hashlib.sha256(one_time_claim.encode()).hexdigest(), hashlib.sha256(key["key"].encode()).hexdigest(),
    ))
    core.clock.t += 1801
    assert get(client, "/agents")["pending_claims"] == []


def test_captain_avatars_are_same_origin_on_console_reads(make):
    core, client = make()
    captain = f"capy:captain:{CAPTAIN}"
    thread = f"capy:thread:{CAPTAIN}"
    core.projects.create(slug="context", name="Context")
    route = core.routes.set(destination="capy", agent="codex", project="context", target={"captain": CAPTAIN},
                            defaults=None, allow_override=True, note="", by="config")
    handle = core.inbox.open_handle(owner="codex", destination="capy", ref="jam_CONVERSATION", project="context",
                                    title="A conversation", meta={}, target=captain)
    thread_handle = core.inbox.open_handle(owner="codex", destination="capy", ref=CAPTAIN, project="context",
                                           title="A thread", meta={}, target=thread)
    for action, target, excerpt in (("delegate", f"captain Captain ({CAPTAIN})", ""), ("delegate", thread, ""),
                                    ("route_set", "codex/context", route.audit_excerpt())):
        core.audit.record(action=action, origin="mcp", outcome="ok", destination="capy", source="codex", client="key:codex",
                          target=target, excerpt=excerpt)

    targets = {row["id"]: row for row in client.portal.call(core.targets) if row["kind"] == "captain"}
    inventory = {row["id"]: row for row in get(client, "/agents")["captains"]}
    url = targets[CAPTAIN]["avatar_url"].removeprefix(PUBLIC)
    assert url.startswith("/avatars/") and "?v=" in url and inventory[CAPTAIN]["avatar_url"] == url
    other_url = inventory[CAPTAIN2]["avatar_url"]
    assert other_url == targets[CAPTAIN2]["avatar_url"].removeprefix(PUBLIC)
    assert other_url != url and "persona" not in inventory[CAPTAIN]
    response = client.get(url)
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"

    board = get(client, "/board")
    assert board["projects"][0]["agents"][0]["avatar_url"] == url
    assert next(row for row in board["unassigned"] if row["id"] == f"capy:captain:{CAPTAIN2}")["avatar_url"] == other_url
    project = get(client, "/board/projects/context")
    assert project["routes"][0]["to"]["avatar_url"] == url
    activity = {row["handle"]: row["endpoint"] for row in project["conversations"]}
    assert activity[handle]["avatar_url"] == url and "avatar_url" not in activity[thread_handle]
    audit = {row["target"]: row["metadata"] for row in get(client, "/audit")["items"]}
    assert audit[f"captain Captain ({CAPTAIN})"]["target_avatar_url"] == url
    assert audit["codex/context"]["target_avatar_url"] == url
    assert "target_avatar_url" not in audit[thread]
    inbox = {row["handle"]: row for row in get(client, "/inbox")["conversations"]}
    assert inbox[handle]["target_avatar_url"] == get(client, f"/conversations/{handle}")["conversation"]["target_avatar_url"] == url
    assert "target_avatar_url" not in inbox[thread_handle]


def test_agent_avatar_projection_drops_untrusted_urls_and_leaves_other_targets_alone(make, monkeypatch):
    core, client = make()
    wrong_values = ["https://elsewhere.test/avatars/example.png?v=1", "//elsewhere.test/avatars/example.png",
                    f"{PUBLIC}/avatars/example.png?key={PRIVATE}", f"{PUBLIC}/avatars/%2fexample.png",
                    f"{PUBLIC}.elsewhere.test/avatars/example.png?v=1", "data:image/png;base64,AAAA",
                    {"url": PRIVATE}, None]

    async def targets():
        rows = [{"kind": "captain", "id": str(i), "address": f"capy:captain:{i}", "block": "capy", "name": f"Captain {i}",
                 "slugs": [], "avatar_url": url, "persona": {"secret": PRIVATE}}
                for i, url in enumerate([*wrong_values, "/avatars/example.png?v=123"])]
        rows.append({**rows[-1], "kind": "dot", "address": "capy:dot:test"})
        return rows

    monkeypatch.setattr(core, "targets", targets)
    inventory = get(client, "/agents")
    assert all("avatar_url" not in row for row in inventory["captains"][:-1])
    assert inventory["captains"][-1]["avatar_url"] == "/avatars/example.png?v=123"
    assert inventory["dots"][0]["avatar_url"] == "/avatars/dots.png"
    assert PRIVATE not in json.dumps(inventory)


def test_cached_captain_avatar_works_without_a_public_base_url(make, monkeypatch):
    core, client = make()
    monkeypatch.setattr(core.avatars, "public_base_url", None)
    captain = next(row for row in get(client, "/agents")["captains"] if row["id"] == CAPTAIN)
    assert captain["avatar_url"].startswith("/avatars/")
    response = client.get(captain["avatar_url"])
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"


def test_inbox_and_thread_use_configured_connector_kind_for_renamed_blocks(make):
    core, client = make(base_config(version=2, blocks={
        "release-bot": {"use": "discord", "connect": False},
    }))
    handle = core.inbox.open_handle(owner="chatgpt", destination="release-bot", ref="engineering",
                                    project=None, title=None, meta={})

    [row] = get(client, "/inbox")["conversations"]
    assert row["destination"] == "release-bot"
    assert row["destination_kind"] == get(client, f"/conversations/{handle}")["conversation"]["destination_kind"] == "discord"


def test_project_and_inbox_reads_keep_safe_cross_system_and_conversation_data(make):
    config = base_config(slack={"workspaces": {"team": {"team_id": "T0123ABCD", "intake": "poll", "watched": ["#dev"],
                                                       "identities": {"bot": {"token_env": "FAKE_SLACK_TOKEN"}}}},
                               "channel_projects": {"team": {"#dev": "context"}}})
    core, client = make(config)
    core.projects.create(slug="context", name="Context", links={
        "capy_project": "Capy workspace", "claude_project": f"https://example.test/{PRIVATE}", "token_project": PRIVATE,
        "repos": ["Owner/Repo", f"https://example.test/{PRIVATE}"],
    })
    core.projects.create(slug="old", name="Old")
    core.projects.update("old", archived=True)
    projects = get(client, "/projects")
    assert projects["projects"][0]["links"] == {"capy_project": "Capy workspace", "repos": ["owner/repo"]}
    assert projects["projects"][1]["archived"]
    [systems] = projects["systems"]
    assert systems["systems"] == {"capy": "Captain (global route)", "capy_project": "Capy workspace",
                                   "repos": "owner/repo", "slack channels": "team/dev"}
    assert "channel:team/#dev" in {r["resource"] for r in get(client, "/visibility")["resources"]}
    assert PRIVATE not in json.dumps(projects)

    wait = core.slack.store.create_request(workspace="team", identity="bot", action="post_message",
                                          params={"channel": "C123", "thread_ts": "18.25", "text": "A pending post", "token": PRIVATE},
                                          persona={"name": "Switchboard", "icon_url": PRIVATE}, key_id="ak_test", ttl_seconds=30)
    core.slack.store.create_request(workspace="team", identity="bot", action="reaction", params={"channel": "C123", "name": "wave"},
                                    persona=None, key_id="ak_test", ttl_seconds=-1)
    handle = core.inbox.open_handle(owner="codex", destination="mailbox", ref="claude", project="context",
                                    title="A conversation", meta={"token": PRIVATE}, status="delivered", stream_cursor=PRIVATE)
    core.inbox.update_handle(handle, state={"token": PRIVATE})
    core.store.x("INSERT INTO conversation_turns(root, turns, paused_at) VALUES (?, ?, ?)", (handle, 200, core.clock()))
    client.portal.call(core.inbox.add, handle, "context", {"text": "A context item", "files": [{"url": PRIVATE}], "bearer": PRIVATE,
                                                         "from": {"kind": "slack", "user": "U123", "trust": "external", "name": {"token": PRIVATE}}})
    client.portal.call(core.inbox.add, handle, "message", {"text": "A second item"})

    inbox = get(client, "/inbox")
    [write] = inbox["pending_writes"]
    assert write["id"] == wait["id"] and write["persona"] == "Switchboard" and write["thread_ts"] == "18.25" and write["text"] == "A pending post"
    assert inbox["turn_cap"] == 200 and inbox["pauses"][0]["root"] == handle
    assert inbox["conversations"][0]["handle"] == handle
    thread = get(client, f"/conversations/{handle}", limit=1)
    [item] = thread["items"]
    assert item["kind"] == "context" and item["text"] == "A context item" and item["from"] == {"kind": "slack", "user": "U123", "trust": "external"}
    assert get(client, f"/conversations/{handle}", cursor=thread["cursor"])["items"][0]["text"] == "A second item"
    assert not core.store.q("SELECT 1 FROM inbox_cursors")
    assert PRIVATE not in json.dumps((inbox, thread))
    assert get(client, f"/conversations/{PRIVATE}", status=404) == {"error": "conversation not found"}


def test_inbox_pages_root_conversations_by_activity_and_project(make):
    clock = Clock()
    core, client = make(clock=clock)

    def open_(handle, project, **meta):
        return core.inbox.open_handle(handle=handle, owner="codex", destination="mailbox", ref="claude",
                                      project=project, title=handle, meta=meta)

    older = open_("older", "context")
    clock.t += 1
    for i in range(21):
        open_(f"context-{i:02}", "context")
    open_("other", "other")
    open_("reply", "context", root=older)
    open_("paused", "other")
    clock.t += 1
    core.inbox.update_handle(older, status="answered")
    core.store.x("INSERT INTO conversation_turns(root, turns, paused_at) VALUES (?, ?, ?)", ("paused", 200, clock()))

    page = get(client, "/inbox")
    assert len(page["conversations"]) == 20 and page["next_cursor"]
    assert page["conversations"][0]["handle"] == older
    first = get(client, "/inbox", project="context")
    assert [row["handle"] for row in first["conversations"]] == [older, *(f"context-{i:02}" for i in range(20, 1, -1))]
    [pause] = first["pauses"]
    assert pause["conversation"]["title"] == "paused"
    next_page = get(client, "/inbox", project="context", cursor=first["next_cursor"])
    assert [row["handle"] for row in next_page["conversations"]] == ["context-01", "context-00"]
    assert next_page["next_cursor"] is None
    assert get(client, "/conversations/context-00")["conversation"]["handle"] == "context-00"
    assert get(client, "/conversations/reply")["conversation"]["handle"] == older
    assert get(client, "/inbox", cursor="nan:older", status=400) == {"error": "invalid cursor"}
    assert get(client, "/inbox", limit=101, status=400) == {"error": "invalid limit"}


def test_memory_folders_search_and_read_use_owner_scope_and_safe_errors(make):
    core, client = make()
    core.projects.create(slug="context", name="Context", private=True)
    note = core.memory.write(source="codex", scope="project:context", namespace="notes/meetings", title="Release target", body="Launch Wednesday",
                             key="release", tags=["planning"])
    listing = get(client, "/memory")
    assert listing["scopes"] == {"general": {}, "project:context": {"notes/meetings": 1}}
    assert not listing["mirror"] and listing["items"][0]["id"] == note["id"] and "body" not in listing["items"][0]
    [hit] = get(client, "/memory", q="Wednesday", scope="project:context", namespace="notes")["items"]
    assert "Wednesday" in hit["snippet"]
    assert get(client, "/memory", q="Wednesday", scope="general")["items"] == []
    for params in ({"id": note["id"]}, {"scope": "project:context", "namespace": "notes/meetings", "key": "release"}):
        entry = get(client, "/memory", **params)["entry"]
        assert entry["body"] == "Launch Wednesday" and entry["history"][0]["source"] == "codex"
        assert set(entry["history"][0]) == {"rev", "at", "source", "action", "title", "scope", "namespace", "key", "tags", "trust", "excerpt"}
    assert get(client, "/memory", id=PRIVATE, status=404) == {"error": "memory not found"}
    assert get(client, "/memory", scope=PRIVATE, status=404) == {"error": "scope not found"}
    assert get(client, "/memory", q="!?", status=400) == {"error": "query has no searchable words"}


def test_console_prompts_match_every_packaged_setup_prompt(make):
    _, client = make()
    sources = {path.name.removesuffix(".md"): path for path in files("switchboard.prompts.setup").iterdir()
               if path.is_file() and path.name.endswith(".md")}
    assert sources
    index = get(client, "/prompts")
    assert index == {"prompts": sorted(sources)}
    for name in index["prompts"]:
        response = client.get(f"/api/prompts/{name}", headers=OWNER)
        assert response.status_code == 200, name
        assert response.headers["content-type"] == "text/markdown; charset=utf-8"
        assert response.content == sources[name].read_bytes(), name


@pytest.mark.parametrize("name", [PRIVATE, "universal-coordinator", "routine", "dispatcher"])
def test_console_prompts_return_not_found_for_unpacked_names(make, name):
    _, client = make()
    assert get(client, f"/prompts/{name}", status=404) == {"error": "prompt not found"}


@pytest.mark.parametrize("name", ["coordinator", "claude-project"])
def test_console_prompt_selection_uses_the_path_name_only(make, name):
    _, client = make()
    path = f"/prompts/{name}"
    response = client.get(f"/api{path}", headers=OWNER,
                          params={"project": "demo", "client": "claude", "claude_project": 'Research "Q4"\n'})
    assert response.status_code == 200, response.text
    assert response.content == client.get(f"/api{path}", headers=OWNER).content
