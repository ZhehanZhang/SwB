import hashlib
import json

import pytest

from conftest import CAPTAIN, CAPY_KEY, OWNER, PUBLIC, STATIC_KEY, base_config, default_env
from switchboard.auth import Principal
from switchboard.core import CoreError
from switchboard.memory import MemoryError_

PRIVATE = "fixture-not-for-display"


def read(client, path, **params):
    response = client.get(f"/api{path}", headers=OWNER, params=params)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def csrf(client):
    return {**OWNER, "Origin": PUBLIC, "X-CSRF-Token": client.get("/auth/session", headers=OWNER).json()["csrf"]}


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/api/agents/detail"),
    ("GET", "/api/agents/claude-context"),
    ("GET", "/api/agents/setup"),
    ("POST", "/api/inbox/pauses/h_missing/resume"),
    ("POST", "/api/memory/mem_missing/move"),
    ("POST", "/api/memory/mem_missing/promote"),
    ("DELETE", "/api/memory/mem_missing"),
])
def test_page_routes_require_access_owner_and_csrf_for_mutations(make, method, path):
    _, client = make()
    for headers in ({}, {"Authorization": f"Bearer {STATIC_KEY}"}):
        response = client.request(method, path, headers=headers)
        assert response.status_code == 401 and response.json() == {"error": "signed_out"}
        assert response.headers["cache-control"] == "no-store"
    if method != "GET":
        assert client.request(method, path, headers=OWNER).status_code == 403
        assert client.request(method, path, headers={**csrf(client), "Origin": "https://other.example.test"}).status_code == 403


def test_agent_detail_links_registered_entities_with_safe_activity(make):
    config = base_config(version=2, blocks={"mcp": {}, "capy": {}, "claude-routine": {},
                                           "grok-routine": {}, "dots": {"delivery": "mailbox", "dots": {"research": {"name": "Research"}}}})
    core, client = make(config, env=default_env())
    core.projects.create(slug="context", name="Context")
    core.projects.create(slug="other", name="Other")
    claude, grok = core.blocks["claude-routine"], core.blocks["grok-routine"]
    token = "sk-ant-oat01-" + "x" * 35
    claude.add(fire="trig_CONTEXT", token=token, project="context", name="claude-context", agent="claude", actor="admin:owner")
    claude._record_fire("claude-context", "ok")
    client.portal.call(grok.save_routine, "grok-context", "context", "key", None)
    grok._record_fire("grok-context", None, "https://grok.example.test", PRIVATE)
    pending = claude.register(fire="trig_PENDING", claude_project="New Claude project", project="new-project",
                              project_name="New project", description=None, create_new=True, name=None, primary=None, actor="claude")
    code = pending["link"].rsplit("/", 1)[-1]

    pairing = core.connections.mint(agent="claude", project="context", client="terminal", by="admin:owner")
    key = core.connections.redeem(pairing["code"])
    core.connections.verify(key["key"])
    other = core.connections.redeem(core.connections.mint(agent="claude", project="other", client=None, by="test")["code"])
    core.routes.set(destination="capy", agent=None, project="context", target={"captain": CAPTAIN},
                    defaults={}, allow_override=False, note="", by="admin")

    common = {"origin": "mcp", "source": "claude", "outcome": "ok", "detail": PRIVATE, "title": PRIVATE, "excerpt": PRIVATE}
    core.audit.record(**common, client=f"connect:{key['key_id']}", destination="core", action="event")
    core.audit.record(**common, client=f"connect:{other['key_id']}", destination="core", action="unrelated")
    core.audit.record(**common, client="owner", destination="claude-routine", action="fire", target="routine:claude-context")
    core.audit.record(**common, client="owner", destination="capy", action="delegate", target=f"captain Captain ({CAPTAIN})")

    inventory = read(client, "/agents")
    assert [(r["name"], r["source"]) for r in inventory["routines"]] == [("claude-context", "stored"), ("grok-context", "stored")]
    assert all(r["created_at"] and r["last_seen_at"] for r in inventory["routines"])
    assert inventory["routines"][0]["created_by"] == "admin:owner"
    assert inventory["keys"][0]["created_by"] == "admin:owner"
    assert inventory["pending_claims"][0]["claim_url"].startswith("/routines/claim/owner/")
    assert inventory["dots"][0]["source"] == "config"
    assert next(r for r in inventory["captains"] if r["id"] == CAPTAIN)["projects"] == ["context"]
    detail = read(client, "/agents/detail", kind="routine", block="claude-routine", id="claude-context")
    assert detail["kind"] == "routine" and detail["agent"] == inventory["routines"][0]
    assert [k["id"] for k in detail["keys"]] == [key["key_id"]]
    assert [r["action"] for r in detail["audit"]] == ["fire", "event"]
    captain = read(client, "/agents/detail", kind="captain", id=CAPTAIN)
    assert {r["name"] for r in captain["routines"]} == {"claude-context", "grok-context"}
    assert captain["audit"][0]["action"] == "delegate"
    assert read(client, "/agents/detail", kind="dot", id="research")["agent"]["name"] == "Research"
    assert read(client, "/agents/detail", kind="key", id=key["key_id"])["agent"]["id"] == key["key_id"]
    assert client.get("/api/agents/detail", headers=OWNER, params={"kind": "key", "id": PRIVATE}).status_code == 404
    assert read(client, "/agents/claude-context") == read(client, "/agents/trig_CONTEXT") == detail
    assert read(client, f"/agents/{CAPTAIN}") == read(client, "/agents/Captain") == captain
    assert read(client, "/agents/research") == read(client, "/agents/Research")
    key_detail = read(client, f"/agents/{key['key_id']}")
    assert key_detail == read(client, "/agents/detail", kind="key", id=key["key_id"])
    mailbox = read(client, "/agents/claude")
    assert mailbox == read(client, "/agents/detail", kind="mailbox", id="claude")
    assert mailbox["kind"] == "mailbox" and mailbox["agent"]["name"] == "claude"
    assert {row["id"] for row in mailbox["keys"]} == {key["key_id"], other["key_id"]}
    assert read(client, "/agents/second")["kind"] == "mailbox"
    response = client.get(f"/api/agents/{PRIVATE}", headers=OWNER)
    assert response.status_code == 404 and response.json() == {"error": "agent_not_found"}
    assert response.headers["cache-control"] == "no-store"
    output = json.dumps((inventory, detail, captain, key_detail, mailbox, response.json()))
    assert all(secret not in output for secret in (
        token, code, key["key"], pairing["code"], hashlib.sha256(code.encode()).hexdigest(), CAPY_KEY, STATIC_KEY, PRIVATE,
    ))


def test_agent_path_detail_disambiguates_display_names(make):
    dots = {"delivery": "mailbox", "dots": {"dot-one": {"name": "Research"}}}
    config = base_config(version=2, blocks={"mcp": {}, "dots": dots, "team-dots": {"use": "dots", **dots}})
    _, client = make(config)

    ambiguous = client.get("/api/agents/Research", headers=OWNER)
    assert ambiguous.status_code == 409 and ambiguous.json() == {"error": "ambiguous_agent"}
    assert read(client, "/agents/Research", kind="dot", block="team-dots")["agent"]["block"] == "team-dots"
    assert client.get("/api/agents/Research", headers=OWNER, params={"kind": "invalid"}).status_code == 400


def test_console_claim_requires_owner_and_keeps_original_link_one_time(make):
    core, client = make(base_config(version=2, blocks={"mcp": {}, "claude-routine": {}}))
    core.projects.create(slug="context", name="Context")
    block = core.blocks["claude-routine"]
    pending = block.register(fire="trig_DEMOCLAIM", claude_project="Claude Context", project="context", project_name=None,
                             description=None, create_new=False, name="claude-context", primary=True, actor="claude")
    original = pending["link"].removeprefix(PUBLIC)
    [row] = read(client, "/agents")["pending_claims"]
    path = row["claim_url"]
    token = "sk-ant-oat01-" + "x" * 35
    denied = client.get(path)
    assert denied.status_code == 303 and denied.headers["location"].startswith("/login?next=%2Froutines%2Fclaim%2Fowner%2F")
    assert client.get(path, headers=OWNER).status_code == client.get(original, headers=OWNER).status_code == 200
    assert client.post(path, data={"token": token}, headers=OWNER).status_code == 403
    assert client.get("/routines/claim/owner/9999999999999999999", headers=OWNER).status_code == 404

    response = client.post(path, data={"token": token, "csrf": csrf(client)["X-CSRF-Token"]}, headers=OWNER)
    assert response.status_code == 200 and token not in response.text
    assert client.get(path, headers=OWNER).status_code == client.get(original, headers=OWNER).status_code == 410
    assert client.post(path, headers=OWNER).status_code == client.post(original, headers=OWNER).status_code == 410
    assert not read(client, "/agents")["pending_claims"]
    assert block.rows()[0]["name"] == "claude-context"


def test_setup_returns_canonical_snippets_without_minting_or_exposing_credentials(make, monkeypatch):
    core, client = make()
    setup = read(client, "/agents/setup")
    chatgpt = setup["chatgpt"]
    assert chatgpt["connector_url"] == f"{PUBLIC}/mcp"
    assert chatgpt["plugins_url"] == "https://chatgpt.com/plugins"
    assert all(text in chatgpt["instruction"] for text in (
        chatgpt["plugins_url"], "web browser", "Add custom MCP server", chatgpt["connector_url"], "OAuth",
    ))
    assert f"Connect to SwB over Streamable HTTP at {PUBLIC}/mcp." in setup["any"]["prompt"]
    assert 'connect(what="mcp-client")' in setup["any"]["prompt"]
    assert "Claude" not in setup["any"]["prompt"]
    agents = {guide["id"]: guide for guide in setup["coding_agents"]}
    assert set(agents) == {"claude-code", "codex", "gemini-cli", "goose", "opencode", "cline", "kiro", "qwen-code", "other"}
    assert agents["claude-code"]["snippet"] == f"claude mcp add --transport http --scope user swb {PUBLIC}/mcp"
    assert agents["codex"]["auth"] == agents["gemini-cli"]["auth"] == "oauth"
    assert "Bearer <key>" in agents["other"]["snippet"]
    assert not core.store.q("SELECT 1 FROM connect_codes")
    assert CAPY_KEY not in json.dumps(setup) and STATIC_KEY not in json.dumps(setup)

    monkeypatch.setattr(core.cfg, "public_url", f"https://user:{PRIVATE}@switchboard.example.test")
    fallback = read(client, "/agents/setup")
    assert fallback["chatgpt"]["connector_url"] is None
    assert fallback["chatgpt"]["plugins_url"] == chatgpt["plugins_url"]
    assert "<public_url>/mcp" in fallback["chatgpt"]["instruction"]
    assert all(guide["auth"] == "bearer" and "Bearer <key>" in guide["snippet"] for guide in fallback["coding_agents"])
    assert PRIVATE not in json.dumps(fallback)
    assert not core.store.q("SELECT 1 FROM connect_codes")


def test_conversation_list_and_thread_follow_root_and_legacy_reply_edges(make):
    core, client = make()
    core.projects.create(slug="context", name="Context")
    claude, codex = Principal("claude", "oauth:claude", "oauth"), Principal("codex", "key:codex", "key")

    async def seed():
        root = (await core.agent_send(codex, to="claude", project="context", text="start"))["handle"]
        reply = (await core.agent_send(claude, to="codex", project="context", text="reply", in_reply_to=root))["handle"]
        legacy = core.inbox.open_handle(owner="claude", destination="mailbox", ref="codex", project="context",
                                        title=None, meta={"private": PRIVATE})
        await core.inbox.add(legacy, "message", {"text": "legacy reply", "in_reply_to": reply})
        outbound = core.inbox.open_handle(owner="codex", destination="capy", ref=CAPTAIN, project="context",
                                          title=None, meta={"target": {"captain": CAPTAIN, "name": "Captain", "token": PRIVATE}},
                                          target=f"capy:captain:{CAPTAIN}")
        await core.inbox.add(outbound, "reply", {"from": {"kind": "capy", "agent": "captain"}, "text": "different thread"})
        return root, reply, legacy, outbound

    root, reply, legacy, outbound = client.portal.call(seed)
    inbox = read(client, "/inbox")
    by_handle = {row["handle"]: row for row in inbox["conversations"]}
    assert set(by_handle) == {root, outbound}
    assert by_handle[root]["first_sender"] == {"kind": "agent", "agent": "codex", "label": "Codex", "auth": "key",
                                                "origin": "mcp", "trust": "owner", "avatar_url": "/avatars/codex.png"}
    assert by_handle[root]["first_target"] == "agent:claude"
    assert by_handle[outbound]["first_sender"] == {"kind": "agent", "agent": "codex", "avatar_url": "/avatars/codex.png"}
    assert by_handle[outbound]["first_target"] == "Captain"
    first = read(client, f"/conversations/{legacy}", limit=2)
    assert first["conversation"]["handle"] == root
    assert [item["handle"] for item in first["items"]] == [root, reply]
    second = read(client, f"/conversations/{root}", cursor=first["cursor"])
    assert [(item["handle"], item["text"]) for item in second["items"]] == [(legacy, "legacy reply")]
    assert PRIVATE not in json.dumps((inbox, first, second))
    assert not core.store.q("SELECT 1 FROM inbox_cursors")


def test_owner_resume_resets_the_canonical_turn_counter_and_audits_the_owner(make):
    core, client = make(base_config(turn_cap=2))
    claude, codex = Principal("claude", "oauth:claude", "oauth"), Principal("codex", "key:codex", "key")

    async def pause():
        root = (await core.agent_send(codex, to="claude", text="start"))["handle"]
        await core.agent_send(claude, to="codex", text="first", in_reply_to=root)
        with pytest.raises(CoreError, match="is paused"):
            await core.agent_send(claude, to="codex", text="turn exceeds cap", in_reply_to=root)
        return root

    root = client.portal.call(pause)
    [paused] = read(client, "/inbox")["pauses"]
    assert paused["root"] == root and paused["turns"] == 2
    path = f"/api/inbox/pauses/{root}/resume"
    assert client.post(path, headers=OWNER).status_code == 403
    assert client.post(path, headers=csrf(client)).json() == {"root": root, "resumed": True}
    assert read(client, "/inbox")["pauses"] == []
    assert client.post(path, headers=csrf(client)).status_code == 404

    async def again():
        await core.agent_send(claude, to="codex", text="after resume", in_reply_to=root)
        await core.agent_send(claude, to="codex", text="another turn", in_reply_to=root)

    client.portal.call(again)
    [resumed] = read(client, "/audit", action="conversation_resume")["items"]
    assert resumed["source"] == "admin:owner@example.test" and resumed["ref"] == root and resumed["outcome"] == "ok"
    assert read(client, "/audit", action="turn_cap")["items"][0]["metadata"] == {"turn_cap": 2}


def test_memory_preview_and_owner_mutations_reuse_scoped_storage_and_emit_safe_audit(make):
    core, client = make()
    core.projects.create(slug="first", name="First", private=True)
    core.projects.create(slug="second", name="Second")
    item = core.memory.write(source="codex", scope="project:first", namespace="notes", title="Note",
                             body=f" First line \n{PRIVATE}", key="note")["id"]
    [listed] = read(client, "/memory", scope="project:first")["items"]
    assert listed["summary"] == "First line" and listed["writer"] == "codex"
    assert PRIVATE not in json.dumps(listed)
    assert read(client, "/memory", q="note")["items"][0]["summary"] == "First line"
    assert read(client, "/memory", id=item)["entry"]["body"].endswith(PRIVATE)

    limited = Principal("test", "key:test", "key", scopes=("project:second",))
    with pytest.raises(MemoryError_, match="Choose a project granted to this key"):
        core.delete_memory(limited, item)
    with pytest.raises(MemoryError_, match="Choose a project granted to this key"):
        core.move_memory(limited, item, "project:second")
    path = f"/api/memory/{item}"
    headers = csrf(client)
    bad = client.post(f"{path}/move", headers=headers, json={"to_scope": PRIVATE})
    assert bad.status_code == 400 and PRIVATE not in bad.text
    assert client.post(f"{path}/move", headers=headers, json={"to_scope": "general"}).status_code == 400
    moved = client.post(f"{path}/move", headers=headers, json={"to_scope": "project:second"}).json()
    assert moved == {"moved": item, "from": "project:first", "to": "project:second"}
    assert client.post(f"{path}/promote", headers=headers).json() == {"moved": item, "from": "project:second", "to": "general"}
    detail = read(client, "/memory", id=item)["entry"]
    assert detail["source"] == "admin:owner@example.test" and detail["writer"] == "codex"
    assert read(client, "/memory", scope="general")["items"][0]["writer"] == "codex"
    assert client.delete(path, headers=headers).json() == {"deleted": item}
    assert client.delete(path, headers=headers).status_code == 404
    assert client.get("/api/memory", headers=OWNER, params={"id": item}).status_code == 404

    first = read(client, "/audit", actions="memory_move,memory_promote", limit=1)
    second = read(client, "/audit", actions="memory_move,memory_promote", limit=1, cursor=first["next_cursor"])
    [promoted], [moved] = first["items"], second["items"]
    assert promoted["project"] == moved["project"] == "second"
    assert promoted["metadata"] == {"from_scope": "project:second", "to_scope": "general", "memory_id": item}
    assert moved["metadata"] == {"from_scope": "project:first", "to_scope": "project:second", "memory_id": item}
    assert second["next_cursor"] is None
    assert PRIVATE not in json.dumps((first, second))
    assert read(client, "/audit", action="memory_delete")["items"][0]["source"] == "admin:owner@example.test"


def test_audit_resolves_recorded_project_without_exposing_diagnostics(make):
    core, client = make()
    core.projects.create(slug="context", name="Context")
    ref = core.inbox.open_handle(owner="codex", destination="capy", ref="jam_example", project="context",
                                 title=PRIVATE, meta={"secret": PRIVATE})
    common = {"origin": "mcp", "source": "codex", "client": "key:codex", "destination": "capy", "outcome": "ok",
              "title": PRIVATE, "excerpt": PRIVATE, "detail": PRIVATE}
    core.audit.record(**common, action="send", ref=ref)
    core.audit.record(**common, action="delegate", ref="jam_example")
    core.audit.record(**common, action="auto_route", target="*/context")
    core.audit.record(**common, action="auto_link", target="context")
    core.audit.record(**common, action="unknown")
    items = {row["action"]: row for row in read(client, "/audit")["items"]}
    assert [items[action]["project"] for action in ("send", "delegate", "auto_route", "auto_link")] == ["context"] * 4
    assert items["unknown"]["project"] is None and items["unknown"]["metadata"] == {}
    assert PRIVATE not in json.dumps(items)


def test_route_audit_keeps_target_name_and_repo_count_at_write_time(make):
    _, client = make()
    headers = csrf(client)
    body = {"destination": "capy", "target": {"captain": CAPTAIN}, "agent": "claude",
            "defaults": {"repos": ["owner/first", "owner/second"]}}
    initial = client.post("/api/routes", headers=headers, json=body)
    assert initial.status_code == 200, initial.text
    assert client.post("/api/routes", headers=headers, json={**body, "defaults": {}}).status_code == 200
    recent, historical = read(client, "/audit", action="route_set")["items"]
    assert recent["metadata"] == {"agent": "claude", "target_name": "Captain", "repo_count": 0}
    assert historical["metadata"] == {"agent": "claude", "target_name": "Captain", "repo_count": 2}
