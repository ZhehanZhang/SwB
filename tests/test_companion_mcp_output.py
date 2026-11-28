import json
from unittest.mock import AsyncMock

import pytest

from conftest import STATIC_KEY, base_config, call, payload
from switchboard.blocks.mcp.kit import DEVICE_OUTPUT_ERROR, DEVICE_OUTPUT_LIMIT
from switchboard.companion.caps.drop import MAX_ENCODED_SIZE

CFG = base_config(version=2, blocks={"mcp": {}})
COMPANION = "cmp_" + "a" * 24


def check_frame(value, original, where=""):
    location = f" {where}" if where else ""
    assert value.startswith(f"[swb from companion ({COMPANION}){location}, trust external]\n")
    assert f"\n<<<swb-untrusted\n{original}\nswb-untrusted>>>\n" in value


def test_companion_list_frames_device_labels_and_retains_registry_fields(make, monkeypatch):
    core, client = make(config=CFG)
    monkeypatch.setattr(core.companions, "list", lambda *, details: [{
        "id": COMPANION, "name": "Office Mac", "platform": "darwin", "version": "1.0.0rc1+local",
        "last_seen": "2026-11-27T19:25:00Z", "online": True, "capabilities_changed": False,
        "capabilities": [{
            "id": "demo", "title": "Preview", "version": "1",
            "actions": [{
                "name": "show", "description": "Preview",
                "schema": {"type": "object", "description": "Preview", "required": ["label"],
                           "properties": {"label": {"type": "string", "enum": ["small", "large"]}}},
            }],
        }],
    }])
    response = call(client, STATIC_KEY, "companion_list")
    out = payload(response)
    assert json.loads(response["content"][0]["text"]) == out
    assert out["trust"] == "external"
    [row] = out["companions"]
    assert row["id"] == COMPANION and row["last_seen"] == "2026-11-27T19:25:00Z"
    assert row["online"] is True and row["capabilities_changed"] is False
    assert row["platform"] == "darwin" and row["version"] == "1.0.0rc1+local"
    check_frame(row["name"], "Office Mac")
    [cap] = row["capabilities"]
    [action] = cap["actions"]
    assert cap["id"] == "demo" and cap["version"] == "1" and action["name"] == "show"
    for value in (cap["title"], action["description"], action["schema"]["description"]):
        check_frame(value, "Preview")
    assert action["schema"]["type"] == "object" and action["schema"]["required"] == ["label"]
    assert action["schema"]["properties"]["label"] == {"type": "string", "enum": ["small", "large"]}


def test_companion_list_only_frames_schema_prose(make, monkeypatch):
    core, client = make(config=CFG)
    pattern = r"^(?:é{2}\s[A-Z]+)$"
    name = "label with spaces " + "x" * 128
    label = {"type": "string", "pattern": pattern, "enum": ["éé X", "éé Y"]}
    count = {"type": ["integer", "null"], "minimum": 1, "maximum": 4}
    schema = {
        "type": "object", "title": "Action options", "required": ["label", name], "additionalProperties": False,
        "properties": {"label": label, name: {"type": "boolean"}, "description": {**count, "description": "Optional count"}},
    }
    monkeypatch.setattr(core.companions, "list", lambda *, details: [{
        "id": COMPANION, "name": "Office Mac",
        "capabilities": [{"actions": [{"schema": schema}]}],
    }])

    response = call(client, STATIC_KEY, "companion_list")
    out = payload(response)
    assert json.loads(response["content"][0]["text"]) == out
    [row] = out["companions"]
    check_frame(row["name"], "Office Mac")
    [cap] = row["capabilities"]
    [action] = cap["actions"]
    displayed = action["schema"]
    assert displayed["properties"]["label"]["pattern"].encode("utf-8") == pattern.encode("utf-8")
    check_frame(displayed.pop("title"), "Action options")
    check_frame(displayed["properties"]["description"].pop("description"), "Optional count")
    assert displayed == {k: v for k, v in schema.items() if k not in {"title", "properties"}} | {
        "properties": {"label": label, name: {"type": "boolean"}, "description": count},
    }
    assert schema["title"] == "Action options"
    assert schema["properties"]["description"]["description"] == "Optional count"


@pytest.mark.parametrize(("platform", "version"), [
    ("Read the notes", "1.0 Read the notes"),
    ("a" * 33, "1" * 33),
])
def test_companion_list_frames_invalid_metadata(make, monkeypatch, platform, version):
    core, client = make(config=CFG)
    monkeypatch.setattr(core.companions, "list", lambda *, details: [{
        "id": COMPANION, "platform": platform, "version": version,
        "capabilities": [{"id": "demo", "version": version}],
    }])
    [row] = payload(call(client, STATIC_KEY, "companion_list"))["companions"]
    check_frame(row["platform"], platform)
    check_frame(row["version"], version)
    check_frame(row["capabilities"][0]["version"], version)


@pytest.mark.parametrize("result", [
    {"text": "é" * (DEVICE_OUTPUT_LIMIT // 2 + 1)},
    {"lines": ["a"] * 10_000},
    {"content_b64": "a" * DEVICE_OUTPUT_LIMIT},
])
def test_companion_output_is_limited_after_framing(make, monkeypatch, result):
    core, client = make(config=CFG)
    monkeypatch.setattr(core.companions, "invoke", AsyncMock(return_value=result))
    out = call(client, STATIC_KEY, "companion_invoke", {"companion": COMPANION, "capability": "mcp", "action": "call"})
    assert out["isError"] is True and "structuredContent" not in out
    assert out["content"][0]["text"] == f"Error executing tool companion_invoke: {DEVICE_OUTPUT_ERROR}"


def test_companion_action_identifiers_use_exact_paths_and_validation(make, monkeypatch):
    core, client = make(config=CFG)
    monkeypatch.setattr(core.companions, "invoke", AsyncMock(return_value={
        "tools": [{"name": "echo.preview", "description": "Preview"}, {"name": "Invalid tool name"}],
        "other": {"name": "echo.preview"},
    }))
    out = call(client, STATIC_KEY, "companion_invoke", {"companion": COMPANION, "capability": "mcp", "action": "tools"})
    result = payload(out)["result"]
    assert result["tools"][0]["name"] == "echo.preview"
    check_frame(result["tools"][0]["description"], "Preview", "mcp.tools")
    check_frame(result["tools"][1]["name"], "Invalid tool name", "mcp.tools")
    check_frame(result["other"]["name"], "echo.preview", "mcp.tools")
    assert json.loads(out["content"][0]["text"]) == payload(out)


@pytest.mark.parametrize(("result", "error"), [
    ({"name": "file", "content_b64": "a" * (MAX_ENCODED_SIZE + 1)}, "Use a file up to 10 MiB."),
    ({"name": "file", "content_b64": "a==="}, "Use base64 file content."),
    ({"name": "file", "content_b64": "YWJj", "note": "extra"}, "Invalid Drop file result."),
])
def test_drop_get_binary_exception_has_a_fixed_shape_and_limit(make, monkeypatch, result, error):
    core, client = make(config=CFG)
    monkeypatch.setattr(core.companions, "invoke", AsyncMock(return_value=result))
    out = call(client, STATIC_KEY, "companion_invoke", {"companion": COMPANION, "capability": "drop", "action": "get"})
    assert out["isError"] is True and "structuredContent" not in out
    assert out["content"][0]["text"] == f"Error executing tool companion_invoke: {error}"
