import json
import re
import tomllib

import pytest
import yaml

from conftest import CAPY_KEY, PUBLIC, STATIC_KEY, base_config, call, payload
from switchboard.auth import Principal

AGENTS = ("claude-code", "codex", "gemini-cli", "goose", "opencode", "cline", "kiro", "qwen-code", "other")


def connect(client, agent=None, token=STATIC_KEY):
    args = {"what": "coding-agent"}
    if agent:
        args["choose"] = {"agent": agent}
    result = call(client, token, "connect", args)
    assert not result.get("isError"), result
    return payload(result)


def entry(agent, snippet):
    if agent == "codex":
        return tomllib.loads(snippet)["mcp_servers"]["switchboard"]
    if agent == "goose":
        return yaml.safe_load(snippet)["extensions"]["switchboard"]
    if agent == "opencode":
        return json.loads(snippet)["mcp"]["servers"]["switchboard"]
    return json.loads(snippet)["mcpServers"]["switchboard"]


def test_discovery_lists_every_agent(make):
    core, client = make()
    [step] = connect(client)["next"]
    assert all(agent in step["do"] for agent in AGENTS)
    assert step["call"]["arguments"]["what"] == "coding-agent"
    assert not core.store.q("SELECT 1 FROM connect_codes")


@pytest.mark.parametrize("agent", AGENTS)
def test_coding_agent_snippet_uses_remote_url_and_oauth_where_supported(make, agent):
    core, client = make()
    out = connect(client, agent)
    [setup] = [step for step in out["next"] if "snippet" in step]
    snippet = setup["snippet"]
    assert f"{PUBLIC}/mcp" in snippet and setup["by"] in {"you", "person"}
    assert "wait or inbox" in out["next"][-1]["do"]
    assert all(key not in json.dumps(out) for key in (CAPY_KEY, STATIC_KEY))
    if agent == "other":
        assert "unverified" in setup["do"]
        assert "Authorization: Bearer <key>" in snippet
        assert core.store.one("SELECT agent FROM connect_codes")["agent"] == agent
    else:
        assert "Bearer" not in snippet and not core.store.q("SELECT 1 FROM connect_codes")
        if agent == "claude-code":
            assert snippet == f"claude mcp add --transport http --scope user switchboard {PUBLIC}/mcp"
            assert "Claude Code channel" in out["next"][-1]["do"]
        else:
            config = entry(agent, snippet)
            assert (config.get("httpUrl") or config.get("url") or config["uri"]) == f"{PUBLIC}/mcp"


@pytest.mark.parametrize("agent", AGENTS)
def test_coding_agent_bearer_configuration_without_oauth(make, agent):
    _, client = make(base_config(auth={}))
    out = connect(client, agent)
    [snippet] = [s["snippet"] for s in out["next"] if "snippet" in s]
    assert f"{PUBLIC}/mcp" in snippet and "Bearer <key>" in snippet
    if agent != "other":
        headers = entry(agent, snippet)
        assert (headers.get("http_headers") or headers["headers"]) == {"Authorization": "Bearer <key>"}
    code = re.search(r'"code":"([A-Z0-9-]+)"', out["next"][0]["do"])[1]
    paired = client.post("/connect", json={"code": code}).json()
    assert paired["agent"] == agent and paired["key"] not in json.dumps(out)
    again = connect(client, agent)
    assert "already has a pairing key" in " ".join(again["done"])
    assert paired["key"] not in json.dumps(again)


def test_coding_agent_with_existing_key_explains_replacement_without_a_snippet(make):
    core, client = make(base_config(auth={}))
    core.projects.create(slug="sample", name="Sample")
    other = core.connections.mint(agent="other", project="sample", client=None, by="owner")
    core.connections.redeem(other["code"])
    first = connect(client, "other")
    code = re.search(r'"code":"([A-Z0-9-]+)"', first["next"][0]["do"])[1]
    paired = client.post("/connect", json={"code": code}).json()
    out = connect(client, "other")
    assert "This agent already has a pairing key" in out["done"]
    assert out["next"] == [{"by": "person", "do": (
        f'To set up another machine or replace it, revoke it with connect_revoke(key_id="{paired["key_id"]}"), then call connect again.'
    )}]
    assert paired["key"] not in json.dumps(out)
    assert len(core.store.q("SELECT 1 FROM connect_codes")) == 2
    assert payload(call(client, STATIC_KEY, "connect_revoke", {"key_id": paired["key_id"]})) == {"revoked": 1}
    assert any("snippet" in step for step in connect(client, "other")["next"])


@pytest.mark.parametrize("agent", ["other", "claude-code"])
def test_limited_coding_agent_has_person_steps_only(make, monkeypatch, agent):
    core, client = make(base_config(auth={}))
    principal = Principal("limited", "key:limited", "key", scopes=("*",))
    monkeypatch.setattr(core.auth, "verify_bearer", lambda _token: principal)
    out = connect(client, agent)
    assert all(step["by"] == "person" and "call" not in step and "link" not in step for step in out["next"])
    assert any(f"{PUBLIC}/mcp" in step.get("snippet", "") for step in out["next"])
    assert not core.store.q("SELECT 1 FROM connect_codes")
