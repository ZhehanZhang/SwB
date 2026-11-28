import re
from importlib.metadata import version

import pytest

from conftest import STATIC_KEY, base_config, rpc

CORE = {"memory_write", "memory_search", "memory_read", "memory_list", "memory_delete", "context_pack", "project_list", "project_create",
        "project_update", "project_sync", "memory_move", "memory_promote", "routes_list", "routes_set", "routes_remove", "routes_resolve", "switchboard_status", "agent_send",
        "modes_status", "space_mode", "memory_view", "memory_create", "memory_str_replace", "memory_insert", "memory_rename",
        "memory_history", "memory_restore", "memory_summary", "memory_sync", "memory_settings",
        "memory_export", "memory_import", "memory_backup_status", "memory_backup_configure",
        "memory_backup_now", "memory_backup_restore"}
GENERIC = {"send", "reply", "wait", "inbox", "get_message", "status", "result", "cancel", "targets", "connect", "connect_code", "connect_list", "connect_revoke", "visibility_list", "visibility_set", "login_link"}
COMPANION = {"companion_code", "companion_list", "companion_invoke", "companion_revoke"}
CAPY = {"capy_delegate", "capy_wait", "capy_inbox", "capy_status", "capy_list", "capy_reply", "capy_result", "capy_captain_digest", "capy_captains", "capy_cancel"}
WEBHOOK = {"webhook_create", "webhook_list", "webhook_rotate", "webhook_revoke", "webhook_test"}
HOOK = {"inbox_ack", "muse_hook_setup", "muse_hook_status", "muse_hook_test", "muse_hook_revoke"}


def tools(c):
    return {t["name"]: t for t in rpc(c, STATIC_KEY, "tools/list").json()["result"]["tools"]}


def test_tool_set_and_schemas(make):
    _, c = make()
    t = tools(c)
    assert set(t) == CORE | GENERIC | COMPANION | CAPY | WEBHOOK | HOOK
    assert t["switchboard_status"]["title"] == "SwB status"
    for name, tool in t.items():
        assert 40 <= len(tool["description"]) <= 700, name
        assert tool["inputSchema"]["type"] == "object"
        assert "ctx" not in tool["inputSchema"].get("properties", {}), name
        assert "annotations" in tool, name
    d = t["capy_delegate"]["inputSchema"]
    assert d["required"] == ["task"] and {"task", "project", "title", "target", "repos", "files", "new_thread", "wait_s"} == set(d["properties"])
    assert set(t["memory_write"]["inputSchema"]["required"]) == {"scope", "namespace", "title", "body"}
    assert t["get_message"]["inputSchema"]["required"] == ["message_id"]
    assert "capy_wait" in t["capy_delegate"]["description"]


def test_capy_tools_only_with_the_capy_destination(make):
    _, c = make(config={"public_url": "https://switchboard.example.test", "auth": {"owner_emails": ["o@example.test"]}})
    assert set(tools(c)) == CORE | GENERIC | COMPANION | WEBHOOK | HOOK


def test_annotations_mark_reads_and_writes(make):
    _, c = make()
    t = tools(c)
    for name in ("memory_search", "memory_read", "memory_list", "capy_status", "capy_result", "capy_list", "capy_captain_digest", "context_pack",
                 "switchboard_status", "routes_list", "routes_resolve", "capy_wait", "capy_inbox", "get_message", "modes_status",
                 "memory_view", "memory_history", "memory_summary"):
        assert t[name]["annotations"]["readOnlyHint"] is True, name
    for name in ("memory_write", "memory_delete", "capy_delegate", "capy_reply", "routes_set", "routes_remove", "connect", "space_mode",
                 "memory_create", "memory_str_replace", "memory_insert", "memory_rename", "memory_restore", "memory_sync", "memory_settings"):
        assert t[name]["annotations"]["readOnlyHint"] is False, name
    assert t["memory_delete"]["annotations"]["destructiveHint"] is True
    for name in ("memory_rename", "memory_restore"):
        assert t[name]["annotations"]["idempotentHint"] is True, name


def test_webhook_schema_declares_provider_schemes_and_annotations(make):
    _, c = make()
    t = tools(c)
    create = t["webhook_create"]["inputSchema"]
    props = create["properties"]
    assert create["required"] == ["name"]
    assert props["direction"]["enum"] == ["in", "out"]
    assert props["verify"]["anyOf"][0]["enum"] == [
        "stripe", "linear", "sentry", "standard", "svix", "hmac-sha256", "hmac-sha1", "bearer", "path",
    ]
    assert props["encoding"]["anyOf"][0]["enum"] == ["hex", "base64"]
    assert all(prop["description"] for prop in props.values())
    assert t["webhook_list"]["annotations"]["readOnlyHint"] is True
    assert t["webhook_revoke"]["annotations"]["destructiveHint"] is True
    assert t["webhook_test"]["annotations"]["openWorldHint"] is True


@pytest.mark.parametrize(("capy_enabled", "slack_enabled"), [(False, False), (True, False), (False, True), (True, True)])
def test_initialize_advertises_swb_and_enabled_block_instructions(make, capy_enabled, slack_enabled):
    blocks = {"mcp": {}, "capy": {"enabled": capy_enabled}, "slack": {"enabled": slack_enabled},
              "dots": {"delivery": "mailbox"}, "claude-routine": {}}
    _, c = make(config=base_config(version=2, blocks=blocks), env={"SWITCHBOARD_KEY_CAPY": STATIC_KEY})
    r = rpc(c, STATIC_KEY, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}).json()
    info = r["result"]["serverInfo"]
    assert (info["name"], info["title"], info["version"]) == ("swb", "SwB", version("swb-one"))
    instructions = r["result"]["instructions"]
    assert instructions.startswith("SwB is a shared workspace")
    assert "Read https://swb.one/docs/get-started#swb=https%3A%2F%2Fswitchboard.example.test." in instructions
    assert re.search(r"\b(switchboard|he|him|his)\b", instructions, re.IGNORECASE) is None
    assert ("capy" in instructions.lower()) == capy_enabled
    assert ("Slack" in instructions) == slack_enabled
    if capy_enabled:
        assert "their Captain" in instructions and "capy_captain_digest" in instructions
    if slack_enabled:
        assert "Posts as the owner wait for their approval." in instructions and "slack_*" in instructions
