from pathlib import Path

import pytest

from conftest import PUBLIC, STATIC_KEY, base_config, call, consent, payload, register, rpc, sign_in

MUSE_CALLBACK = "https://agent.meta.ai/api/hatch/oauth/callback"


@pytest.mark.parametrize("redirect", [
    "https://agent.meta.ai/evil",
    "https://evil.agent.meta.ai.attacker.com/api/hatch/oauth/callback",
    "http://agent.meta.ai/api/hatch/oauth/callback",
    "http://agent.meta.ai/evil",
    f"{MUSE_CALLBACK}/",
    f"{MUSE_CALLBACK}?next=other",
])
def test_muse_callback_variants_are_rejected(make, redirect):
    _, client = make()
    rejected = client.post("/register", json={"redirect_uris": [redirect]})
    assert rejected.status_code == 400
    assert rejected.json()["error"] == "invalid_redirect_uri"

    registered = register(client, redirect=MUSE_CALLBACK, client_name="Custom connector")
    page, request = consent(client, registered["client_id"], redirect=redirect)
    assert page.status_code == 400 and request is None
    assert "location" not in page.headers


def test_muse_signs_in_as_a_hosted_oauth_agent(make):
    _, client = make()
    registered = register(client, redirect=MUSE_CALLBACK, client_name="Custom connector")
    assert registered["redirect_uris"] == [MUSE_CALLBACK]
    token = sign_in(client, redirect=MUSE_CALLBACK, client_id=registered["client_id"])["access_token"]
    status = payload(call(client, token, "switchboard_status"))
    assert status["you"] == {"agent": "muse", "auth": "oauth", "origin": "mcp"}


@pytest.mark.parametrize("redirect", [
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://grok.com/connectors/oauth/callback",
    MUSE_CALLBACK,
])
def test_hosted_oauth_clients_have_the_same_tool_roles_as_owner(make, redirect):
    _, client = make()
    token = sign_in(client, redirect=redirect)["access_token"]
    tools = rpc(client, token, "tools/list").json()["result"]["tools"]
    owner_tools = rpc(client, STATIC_KEY, "tools/list").json()["result"]["tools"]
    assert tools == owner_tools
    assert {"connect", "inbox", "wait", "get_message", "reply", "project_create"} <= {tool["name"] for tool in tools}
    assert payload(call(client, token, "project_create", {"slug": "example", "name": "Example"}))["slug"] == "example"


@pytest.mark.parametrize("owner_sign_in", [True, False])
def test_connect_muse_requires_public_https_and_owner_sign_in(make, owner_sign_in):
    config = base_config(public_url=None)
    if not owner_sign_in:
        config["auth"] = {}
    core, client = make(config)
    assert core.cfg.owner_sign_in is owner_sign_in
    setup = payload(call(client, STATIC_KEY, "connect", {"what": "muse"}))
    assert setup["what"] == "muse" and setup["done"] == []
    [step] = setup["next"]
    assert step["by"] == "person"
    assert "swb up --tunnel cloudflare" in step["do"]
    assert "https://swb.one/docs/first-run#get-a-public-address" in step["do"]
    assert ("Set up owner sign-in for OAuth." in step["do"]) is not owner_sign_in


def test_connect_muse_guides_the_person_until_muse_signs_in(make):
    _, client = make()
    sign_in(client)
    args = {"what": "muse"}
    out = payload(call(client, STATIC_KEY, "connect", args))
    assert out == {
        "what": "muse",
        "complete": False,
        "guide": {"prompt": "setup-muse", "resource": "swb://setup/muse"},
        "done": [f"MCP URL: {PUBLIC}/mcp.", "OAuth sign-in: available."],
        "next": [{"by": "person", "do": (
            f"In a Muse chat, ask Muse to add {PUBLIC}/mcp as a custom connector with OAuth. "
            "Open the sign-in link Muse sends."
        )}],
    }

    tokens = sign_in(client, redirect=MUSE_CALLBACK)
    for caller in (STATIC_KEY, tokens["access_token"]):
        assert payload(call(client, caller, "connect", args)) == {
            **out, "done": [*out["done"], "The client is signed in."], "next": [], "state": "connected", "complete": True,
        }


def test_muse_prompts_match():
    root = Path(__file__).resolve().parents[1]
    assert (root / "src/switchboard/prompts/setup/muse.md").read_bytes() == (root / "docs/prompts/muse.md").read_bytes()


def test_muse_receives_and_replies_to_mailbox_messages(make):
    _, client = make()
    token = sign_in(client, redirect=MUSE_CALLBACK)["access_token"]
    task = "Reply with pong."
    sent = payload(call(client, STATIC_KEY, "send", {"to": "agent:muse", "task": task}))
    assert sent["to"] == "agent:muse" and sent["mailbox"]
    [item] = payload(call(client, token, "wait", {"timeout_s": 0}))["items"]
    assert item["text"] == task and item["handle"] == sent["conversation"]
    message = payload(call(client, token, "get_message", {"message_id": item["message_id"]}))
    assert message == item
    reply = payload(call(client, token, "reply", {"conversation": item["handle"], "text": "pong", "final": True}))
    assert reply["delivered"]
    [received] = payload(call(client, STATIC_KEY, "inbox", {"since_cursor": 0}))["items"]
    assert received["text"] == "pong" and received["from"]["agent"] == "muse"
