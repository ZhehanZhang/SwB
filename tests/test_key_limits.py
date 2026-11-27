"""auth.key_tools and auth.key_targets: a key held to some tools and some send targets, enforced in the server."""

from conftest import SECOND_KEY, STATIC_KEY, arpc, base_config, call, error_text, payload, rpc, beacon


def limited(make):
    cfg = base_config()
    cfg["auth"]["key_tools"] = {"second": ["send"]}
    cfg["auth"]["key_targets"] = {"second": ["agent:claude"]}
    cfg["auth"]["key_scopes"] = {"second": ["project:beacon"]}
    core, c = make(config=cfg)
    beacon(core)
    core.projects.create(slug="other", name="Other")
    return core, c


def test_tools_list_shows_only_the_allowed_tools(make):
    _, c = limited(make)
    names = {t["name"] for t in rpc(c, SECOND_KEY, "tools/list").json()["result"]["tools"]}
    assert names == {"send"}
    assert len({t["name"] for t in rpc(c, STATIC_KEY, "tools/list").json()["result"]["tools"]}) > 10


def test_a_disallowed_tool_is_refused(make):
    _, c = limited(make)
    for tool, args in (("memory_search", {"query": "x", "project": "beacon"}), ("inbox", {}), ("switchboard_status", {}),
                       ("reply", {"conversation": "h_x", "text": "hi"}), ("slack_post", {"workspace": "a", "channel": "#x", "text": "hi"})):
        assert "may call only send" in error_text(call(c, SECOND_KEY, tool, args)), tool


def test_the_allowed_target_is_accepted(make):
    core, c = limited(make)
    out = payload(call(c, SECOND_KEY, "send", {"to": "agent:claude", "task": "status went red", "project": "beacon"}))
    assert out["delivered"] and out["to"] == "agent:claude"
    assert "status went red" in str(core.inbox.read(owner="claude")["items"][-1])


def test_a_disallowed_target_is_refused(make):
    _, c = limited(make)
    for to in ("agent:capy", "capy", "claude-code", None):
        args = {"task": "x", "project": "beacon", **({"to": to} if to else {})}
        assert "second key" in error_text(call(c, SECOND_KEY, "send", args)), to


def test_a_disallowed_project_or_a_forward_is_refused(make):
    _, c = limited(make)
    assert "limited to project:beacon" in error_text(call(c, SECOND_KEY, "send", {"to": "agent:claude", "task": "x", "project": "other"}))
    assert "can't forward" in error_text(call(c, SECOND_KEY, "send", {"to": "agent:claude", "task": "x", "project": "beacon", "forward": "h_x"}))


def test_a_limited_key_cannot_read_an_inbox_over_http(make):
    _, c = limited(make)
    assert c.get("/inbox/poll", headers={"Authorization": f"Bearer {SECOND_KEY}"}, params={"timeout": "0"}).status_code == 403
    assert c.get("/inbox/poll", headers={"Authorization": f"Bearer {STATIC_KEY}"}, params={"timeout": "0"}).status_code == 200


def test_key_names_from_env_use_hyphens(make, tmp_path):
    from switchboard.config import load_config

    cfg = load_config(None, env={"SWITCHBOARD_KEY_STATUS_MONITOR": "k" * 32})
    assert [k.name for k in cfg.static_keys] == ["status-monitor"]


async def test_a_task_mode_call_is_held_to_the_allowlist_too(amake):
    cfg = base_config()
    cfg["auth"]["key_tools"] = {"second": ["send"]}
    core, c = await amake(config=cfg)
    r = await arpc(c, SECOND_KEY, "tools/call", {"name": "memory_search", "arguments": {"query": "x"}}, tasks=True)
    assert "may call only send" in r.text


def test_auth_maps_match_a_key_name_spelt_with_underscores(make):
    from switchboard.config import load_config

    cfg = load_config(None, env={"SWITCHBOARD_KEY_STATUS_MONITOR": "k" * 32})
    assert cfg.static_keys[0].name == "status-monitor"
    cfg = base_config()
    cfg["auth"]["key_scopes"] = {"SECOND": ["project:beacon"]}
    cfg["auth"]["key_tools"] = {"sec_ond": ["send"]}
    core, _ = make(config=cfg)
    assert core.cfg.key_scopes == {"second": ("project:beacon",)} and core.cfg.key_tools == {"sec-ond": ("send",)}


def test_the_legacy_agent_send_alias_is_held_to_the_targets(make):
    cfg = base_config()
    cfg["auth"]["key_targets"] = {"second": ["agent:claude"]}
    core, c = make(config=cfg)
    assert "may send only to" in error_text(call(c, SECOND_KEY, "agent_send", {"to": "capy", "text": "x"}))
    assert payload(call(c, SECOND_KEY, "agent_send", {"to": "claude", "text": "x"}))["delivered"]
