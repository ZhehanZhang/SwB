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
        "id": COMPANION, "name": "Office Mac", "platform": "darwin", "version": "1.0",
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
    out = payload(call(client, STATIC_KEY, "companion_list"))
    assert out["trust"] == "external"
    [row] = out["companions"]
    assert row["id"] == COMPANION and row["last_seen"] == "2026-11-27T19:25:00Z"
    assert row["online"] is True and row["capabilities_changed"] is False
    for field, text in (("name", "Office Mac"), ("platform", "darwin"), ("version", "1.0")):
        check_frame(row[field], text)
    [cap] = row["capabilities"]
    [action] = cap["actions"]
    assert cap["id"] == "demo" and action["name"] == "show"
    for value in (cap["title"], action["description"], action["schema"]["description"]):
        check_frame(value, "Preview")
    assert action["schema"]["type"] == "object" and action["schema"]["required"] == ["label"]
    assert action["schema"]["properties"]["label"] == {"type": "string", "enum": ["small", "large"]}


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
