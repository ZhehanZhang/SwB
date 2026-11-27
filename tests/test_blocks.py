"""Step 1 of the block design: one Block interface, the agents registry, generic verbs with capy_* aliases, the v2
config with its v1 translator, the additive handles migration, pairing codes, and route seeds."""

import sqlite3

import yaml

from conftest import CAPTAIN, OWNER, PROJECT, STATIC_KEY, base_config, beacon, call, error_text, payload, register, rpc, sign_in

from switchboard.blocks import BLOCKS, Address, load
from switchboard.config import load_config, migrate_text, translate_v1
from switchboard.store import Store
from switchboard.targets import by_slug, parse


def test_every_block_declares_a_manifest():
    for use in BLOCKS:
        m = load(use).manifest.as_dict()
        assert m["id"] == use and m["kind"] in ("source", "sink", "both") and set(m["level"]) == {"out", "in"}
    capy = load("capy").manifest
    assert {a.id for a in capy.agents} == {"capy"} and capy.verbs["send"] == "delegate" and "capy_project" in capy.link_keys
    assert load("mcp").manifest.agents == () and load("slack").manifest.agents == ()


def test_old_module_paths_are_the_same_modules():
    import switchboard.blocks.capy.stream as new
    import switchboard.destinations.capy.stream as old
    from switchboard.destinations import Destination, DestinationError
    from switchboard.origins import Origin
    from switchboard.origins.mcp import WaitCaps
    from switchboard.blocks import Block, BlockError
    from switchboard.blocks.mcp import WaitCaps as NewWaitCaps

    assert old is new and Destination is Block and Origin is Block and DestinationError is BlockError and WaitCaps is NewWaitCaps


def test_v1_config_translates_to_blocks(tmp_path):
    v1 = {"public_url": "https://sb.example.test", "origins": {"mcp": {"wait": {"max_s": 100}}, "slack": {"workspaces": {"a2a": {"project": "orbit"}}, "refresh_minutes": 5}},
          "destinations": {"capy": {"daily_cap": 3}}, "slack": {"workspaces": {"a2a": {"bot_token_env": "X"}}, "captain_avatars": True}}
    v2 = translate_v1(v1)
    assert v2["version"] == 2 and v2["public_url"] == "https://sb.example.test"
    assert v2["blocks"]["mcp"] == {"use": "mcp", "wait": {"max_s": 100}}
    assert v2["blocks"]["capy"] == {"use": "capy", "daily_cap": 3}
    assert v2["blocks"]["slack"]["workspace_projects"] == {"a2a": "orbit"} and v2["blocks"]["slack"]["refresh_minutes"] == 5
    assert v2["blocks"]["slack"]["workspaces"] == {"a2a": {"bot_token_env": "X"}} and v2["blocks"]["slack"]["captain_avatars"] is True
    p1, p2 = tmp_path / "v1.yaml", tmp_path / "v2.yaml"
    p1.write_text(yaml.safe_dump(v1))
    p2.write_text(migrate_text(p1.read_text()))
    a, b = load_config(p1, {}), load_config(p2, {})
    assert a.blocks == b.blocks and list(a.blocks) == ["mcp", "slack", "capy"] and b.version == 2 and a.version == 1
    assert migrate_text(p2.read_text()) == p2.read_text()
    assert list(load_config(None, {}).blocks) == ["mcp"]
    assert translate_v1({"destinations": {"capy": {"enabled": False}}})["blocks"]["capy"]["enabled"] is False
    assert translate_v1({"origins": {"slack": {}}})["blocks"]["slack"]["engine"] is False


def test_v2_config_runs_the_same_core(make):
    cfg = translate_v1(base_config())
    core, c = make(config=cfg)
    assert set(core.blocks) == {"mcp", "capy"} and core.health()["blocks"]["capy"]["level"] == {"out": "L4", "in": "mcp"}
    assert not call(c, STATIC_KEY, "send", {"task": "from a v2 config"}).get("isError")


def test_agents_come_from_presets_and_blocks(make):
    core, c = make()
    assert {"claude", "chatgpt", "gemini", "claude-code", "capy"} <= set(core.agents.ids())
    assert "https://capy.ai/web/callbacks/mcp" in core.auth.redirect_allow and "capy.ai" in core.auth.cimd_hosts
    assert register(c, "https://capy.ai/web/callbacks/mcp").get("client_id")
    bare, c2 = make(config={"public_url": "https://switchboard.example.test", "auth": {"owner_emails": ["o@example.test"]}})
    assert "capy" not in bare.agents.ids() and "https://capy.ai/web/callbacks/mcp" not in bare.auth.redirect_allow
    assert "capy_project" not in bare.projects.link_keys and "claude_project" in bare.projects.link_keys


def test_send_reply_and_wait_through_mailboxes(make):
    core, c = make()
    beacon(core)
    tok = sign_in(c)["access_token"]
    sent = payload(call(c, STATIC_KEY, "send", {"task": "please review", "to": "agent:claude", "project": "beacon"}))
    assert sent["delivered"] and sent["to"] == "agent:claude"
    got = payload(call(c, tok, "wait", {"timeout_s": 0}))["items"]
    assert [i["text"] for i in got] == ["please review"] and got[0]["from"]["agent"] == "capy"
    back = payload(call(c, tok, "reply", {"conversation": got[0]["handle"], "text": "looks good"}))
    assert back["to"] == "agent:capy"
    assert [i["text"] for i in payload(call(c, STATIC_KEY, "inbox", {"since_cursor": 0}))["items"]] == ["looks good"]
    assert payload(call(c, STATIC_KEY, "send", {"task": "bare name", "to": "claude"}))["to"] == "agent:claude"
    assert "no conversation" in error_text(call(c, tok, "reply", {"conversation": sent["handle"] + "x", "text": "x"}))


def test_send_without_to_follows_the_route_and_verbs_reach_the_block(make, capy):
    core, c = make()
    out = payload(call(c, STATIC_KEY, "send", {"task": "route me"}))
    assert out["block"] == "capy" and out["conversation"] == out["handle"] and out["thread_id"] == CAPTAIN
    assert core.inbox.handle(out["handle"])["target"] == f"capy:captain:{CAPTAIN}"
    st = payload(call(c, STATIC_KEY, "status", {"conversation": out["handle"]}))
    assert st["id"] == CAPTAIN
    assert payload(call(c, STATIC_KEY, "result", {"conversation": out["handle"]}))["id"] == CAPTAIN
    assert payload(call(c, STATIC_KEY, "cancel", {"conversation": out["handle"]}))["status"] == "cancelled"
    explicit = payload(call(c, STATIC_KEY, "send", {"task": "to the captain by slug", "to": "bcn-1"}))
    assert explicit["thread_id"] == CAPTAIN
    assert "no target" in error_text(call(c, STATIC_KEY, "send", {"task": "x", "to": "nobody-here"}))


def test_mailbox_send_records_the_ask_or_explicit_title(make):
    core, client = make()
    token = sign_in(client)["access_token"]
    task = "Check the canary.\n\nReply with the result."
    send = {"task": task, "to": "agent:claude"}
    first = payload(call(client, STATIC_KEY, "send", send))
    second = payload(call(client, STATIC_KEY, "send", {**send, "title": "**Canary review**"}))
    items = payload(call(client, token, "wait", {"timeout_s": 0}))["items"]
    first_item, second_item = items
    assert first_item["subject"] == f"{core.agents.label('capy')} asks: Check the canary. Reply with the result."
    assert second_item["subject"] == "Canary review"
    for item, sent in zip(items, (first, second), strict=True):
        assert item["subject"] == core.inbox.handle(sent["handle"])["title"]
        assert item["text"] == task and item["text"].endswith("Reply with the result.")


def test_capy_captain_wake_leads_with_the_title_once(make, capy):
    core, client = make()
    title = "Dana asks: check the canary"
    tail = "Reply with the result."
    first = payload(call(client, STATIC_KEY, "send", {"title": title, "task": f"Look at the build.\n\n{tail}"}))
    ready = f"{title}\n\nLook at the deploy.\n\n{tail}"
    second = payload(call(client, STATIC_KEY, "send", {"title": title, "task": ready}))
    assert first["thread_id"] == second["thread_id"] == CAPTAIN
    assert core.inbox.handle(first["handle"])["title"] == title
    first_message, second_message = capy.messages[CAPTAIN][-2:]
    assert first_message["text"] == f"{title}\n\nLook at the build.\n\n{tail}"
    assert second_message["text"] == ready
    core.projects.create(slug="widgets", name="Widgets")
    core.routes.set(destination="capy", agent=None, project="widgets", target={"project": PROJECT},
                    defaults={}, allow_override=False, note="", by="test")
    new = payload(call(client, STATIC_KEY, "send", {"title": title, "task": "Check new results.", "project": "widgets"}))
    assert new["thread_id"] != CAPTAIN and capy.threads[new["thread_id"]]["title"] == title


def test_targets_list_blocks_agents_and_projects(make):
    core, c = make()
    beacon(core)
    got = payload(call(c, STATIC_KEY, "targets", {"refresh": True}))["targets"]
    addresses = {t["address"] for t in got}
    assert {f"capy:captain:{CAPTAIN}", "agent:claude", "project:beacon"} <= addresses
    cap = next(t for t in got if t["address"] == f"capy:captain:{CAPTAIN}")
    assert cap["level"] == "L4" and cap["slugs"] == ["captain", "bcn-1"] and cap["persona"]["id"] == "captain"
    assert next(t for t in got if t["address"] == "project:beacon")["routed_to"] == f"capy:captain:{CAPTAIN}"
    assert [t["address"] for t in payload(call(c, STATIC_KEY, "targets", {"block": "agent", "query": "claude"}))["targets"]] == ["agent:claude", "agent:claude-code"]


def test_slug_priority_and_aliases():
    ts = [{"address": "agent:captain", "block": "agent", "kind": "agent", "id": "captain", "slugs": ["captain"]},
          {"address": "capy:captain:jam_1", "block": "capy", "kind": "captain", "id": "jam_1", "slugs": ["captain"]},
          {"address": "project:captain", "block": "project", "kind": "project", "id": "captain", "slugs": ["captain"]}]
    assert by_slug(ts)["captain"]["block"] == "capy"
    assert by_slug(ts, ["project"])["captain"]["block"] == "project"
    assert by_slug(ts, aliases={"boss": "capy:captain:jam_1"})["boss"]["id"] == "jam_1"
    assert parse("capy:captain:jam_1", ["capy"]) == Address("capy", "captain", "jam_1") and parse("hello", ["capy"]) is None


def test_capy_aliases_still_work_are_marked_and_counted(make):
    core, c = make()
    tools = {t["name"]: t for t in rpc(c, STATIC_KEY, "tools/list").json()["result"]["tools"]}
    for name in ("capy_delegate", "capy_wait", "capy_inbox", "capy_status", "capy_reply", "capy_result", "capy_cancel", "agent_send"):
        assert tools[name]["description"].startswith("Deprecated alias of"), name
    assert not tools["send"]["description"].startswith("Deprecated") and not tools["capy_list"]["description"].startswith("Deprecated")
    out = payload(call(c, STATIC_KEY, "capy_delegate", {"task": "old name", "target": f"captain:{CAPTAIN}"}))
    assert out["thread_id"] == CAPTAIN
    payload(call(c, STATIC_KEY, "capy_wait", {"timeout_s": 0}))
    counts = {(r["alias"], r["agent"]): r["count"] for r in core.alias_counts()}
    assert counts == {("capy_delegate", "capy"): 1, ("capy_wait", "capy"): 1}
    init = rpc(c, STATIC_KEY, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}).json()
    text = init["result"]["instructions"]
    assert text.index("Use send to hand off work") < text.index("Capy is a coding agent")


def test_aliases_can_be_switched_off(make):
    cfg = base_config(origins={"mcp": {"legacy_aliases": False}})
    _, c = make(config=cfg)
    names = {t["name"] for t in rpc(c, STATIC_KEY, "tools/list").json()["result"]["tools"]}
    assert "send" in names and "capy_list" in names and not {"capy_delegate", "capy_wait", "agent_send"} & names


def test_handles_gain_a_target_column_without_losing_rows(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("""CREATE TABLE handles (handle TEXT PRIMARY KEY, owner TEXT NOT NULL, destination TEXT NOT NULL, ref TEXT NOT NULL, project TEXT,
      title TEXT, meta TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, updated_at REAL, open INTEGER NOT NULL DEFAULT 1, status TEXT,
      stream_cursor TEXT, state TEXT NOT NULL DEFAULT '{}');
      INSERT INTO handles(handle, owner, destination, ref, created_at) VALUES ('h_old', 'claude', 'capy', 'jam_X', 1);""")
    con.commit()
    con.close()
    store = Store(db)
    cols = {r[1] for r in store.q("PRAGMA table_info(handles)")}
    assert "target" in cols and dict(store.one("SELECT handle, ref, target FROM handles")) == {"handle": "h_old", "ref": "jam_X", "target": None}
    store.close()
    Store(db).close()


def test_pairing_code_connects_an_agent_once_and_revokes(make):
    core, c = make()
    code = payload(call(c, STATIC_KEY, "connect_code", {"agent": "codex", "client": "codex-cli"}))
    assert len(code["code"]) == 11 and code["agent"] == "codex" and code["code"] in code["curl"]
    bad = c.post("/connect", json={"code": "WRONG-CODE0"})
    assert bad.status_code == 400
    got = c.post("/connect", json={"code": code["code"].lower()})
    assert got.status_code == 200 and got.headers["cache-control"] == "no-store"
    key = got.json()["key"]
    assert key.startswith("swb_") and got.json()["mcp_url"].endswith("/mcp")
    assert c.post("/connect", json={"code": code["code"]}).status_code == 400
    me = payload(call(c, key, "switchboard_status"))["you"]
    assert me == {"agent": "codex", "auth": "connect", "origin": "mcp"}
    assert "codex" in core.known_agents() and "codex" in payload(call(c, STATIC_KEY, "switchboard_status"))["connected"]
    assert "connected agent" in error_text(call(c, key, "connect_code", {"agent": "x"}))
    kid = got.json()["key_id"]
    assert payload(call(c, STATIC_KEY, "connect_list"))["keys"][0]["id"] == kid
    assert payload(call(c, STATIC_KEY, "connect_revoke", {"key_id": kid}))["revoked"] == 1
    assert rpc(c, key, "tools/list").status_code == 401
    assert "lowercase" in error_text(call(c, STATIC_KEY, "connect_code", {"agent": "Slack"}))
    assert sqlite3.connect(core.store.path).execute("SELECT count(*) FROM agent_keys WHERE key_hash = ?", (key,)).fetchone()[0] == 0


def test_connected_agents_count_as_the_owner_but_cannot_change_access(make, capy):
    core, c = make()
    code = payload(call(c, STATIC_KEY, "connect_code", {"agent": "codex"}))["code"]
    key = c.post("/connect", json={"code": code}).json()["key"]
    assert payload(call(c, key, "send", {"task": "route me"}))["block"] == "capy"
    item = payload(call(c, key, "send", {"task": "fyi", "to": "agent:claude"}))
    assert item["delivered"]
    tok = sign_in(c)["access_token"]
    (got,) = payload(call(c, tok, "wait", {"timeout_s": 0}))["items"]
    assert got["from"]["agent"] == "codex" and got["from"]["trust"] == "owner"
    assert "connected agent" in error_text(call(c, key, "visibility_set", {"resource": "target:captain", "visibility": "shared"}))


def test_visibility_tools_flip_resources_and_keep_tools_private(make):
    core, c = make(config=base_config(version=2, blocks={
        "mcp": {},
        "slack": {"workspaces": {"orbit": {"team_id": "TORBIT01", "intake": "none", "watched": ["#dev"],
                                          "identities": {"bot": {"token_env": "ORBIT_BOT_TOKEN"}}}}},
    }))
    assert payload(call(c, STATIC_KEY, "visibility_set", {"resource": "target:Claude", "visibility": "shared"}))["visibility"] == "shared"
    assert payload(call(c, STATIC_KEY, "visibility_set", {"resource": "channel:orbit/#dev", "visibility": ["U0FRIEND0", "workspace:orbit"]}))["visibility"] == ["U0FRIEND0", "workspace:orbit"]
    assert "always private" in error_text(call(c, STATIC_KEY, "visibility_set", {"resource": "tool:slack_post", "visibility": "shared"}))
    assert "not a resource" in error_text(call(c, STATIC_KEY, "visibility_set", {"resource": "helperbot", "visibility": "shared"}))
    assert "Unknown visibility resource" in error_text(call(c, STATIC_KEY, "visibility_set", {"resource": "target:helperbot", "visibility": "shared"}))
    rows = {r["resource"]: r for r in payload(call(c, STATIC_KEY, "visibility_list"))["resources"]}
    assert rows["target:claude"]["visibility"] == "shared" and rows["target:claude"]["source"] == "override"
    assert core.visibility.rule("target:other") == "private"
    assert any(a["action"] == "visibility_set" for a in core.audit.recent(limit=10))



def test_owner_api_shows_and_flips_target_visibility(make):
    core, c = make()
    csrf = c.get("/auth/session", headers=OWNER).json()["csrf"]
    path = "/api/visibility"
    body = {"resource": "target:captain", "visibility": "shared"}
    assert c.post(path, json=body, headers={**OWNER, "X-CSRF-Token": "nope"}).status_code == 403
    headers = {**OWNER, "X-CSRF-Token": csrf}
    assert c.post(path, json=body, headers=headers).status_code == 200
    assert core.visibility.rule("target:captain") == "shared"
    rows = {row["resource"]: row for row in c.get(path, headers=OWNER).json()["resources"]}
    assert rows["target:captain"]["visibility"] == "shared"
    assert c.post(path, json={**body, "resource": "tool:deploy"}, headers=headers).status_code == 400


def test_pairing_form_mints_a_code_and_tokens_api_revokes_the_key(make):
    core, c = make()
    csrf = c.get("/auth/session", headers=OWNER).json()["csrf"]
    assert c.post("/pairing/code", data={"agent": "codex", "csrf": "nope"}, headers=OWNER).status_code == 403
    shown = c.post("/pairing/code", data={"agent": "codex", "csrf": csrf}, headers=OWNER)
    assert shown.status_code == 200 and "https://switchboard.example.test/connect" in shown.text
    code = shown.text.split("&quot;code&quot;: &quot;")[1][:11]
    got = c.post("/connect", json={"code": code})
    assert got.status_code == 200, (code, got.text)
    kid = got.json()["key_id"]
    assert "Revoke it in Tokens at /tokens" in got.json()["note"]
    assert any(row["revoke_id"] == kid for row in c.get("/api/tokens", headers=OWNER).json()["stored"])
    response = c.post(f"/api/pairing/keys/{kid}/revoke", headers={**OWNER, "X-CSRF-Token": csrf})
    assert response.status_code == 200 and response.json() == {"id": kid, "revoked": True}
    assert all(row["revoke_id"] != kid for row in c.get("/api/tokens", headers=OWNER).json()["stored"])
    assert core.connections.list() == []


def test_config_routes_seed_the_global_default_once(make, capy):
    cfg = base_config(destinations={"capy": {}}, routes=[{"match": {}, "to": f"capy:captain:{CAPTAIN}"}])
    core, c = make(config=cfg)
    r = core.routes.resolve(destination="capy", agent="claude", project=None)[0]
    assert r.level == "global" and r.target == {"captain": CAPTAIN} and r.updated_by == "config"
    assert payload(call(c, STATIC_KEY, "send", {"task": "seeded"}))["thread_id"] == CAPTAIN
