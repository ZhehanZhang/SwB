import json
from urllib.parse import urlparse

import httpx
import pytest

from conftest import CAPTAIN, CAPTAIN2, CAPY_KEY, OTHER_PROJECT, OWNER, SERVICE_KEY, STATIC_KEY, base_config, call, default_env, new_core, payload
from switchboard.auth import Principal
from switchboard.config import translate_v1
from test_project_routines import TOKEN


def config(**capy_config):
    cfg = translate_v1(base_config(destinations={"capy": capy_config}))
    cfg["blocks"]["claude-routine"] = {"use": "claude-routine"}
    return cfg


def connect(client, what, *, project=None, choose=None):
    return payload(call(client, STATIC_KEY, "connect", {"what": what, "project": project, "choose": choose}))


@pytest.mark.parametrize("paired", [False, True], ids=["owner", "paired"])
def test_cloudflare_connect_returns_manual_setup(make, monkeypatch, paired):
    core, client = make(config=base_config(version=2, blocks={"mcp": {}}))
    key = STATIC_KEY
    if paired:
        pairing = core.connections.mint(agent="fixture-agent", project=None, client=None, by="owner")
        key = core.connections.redeem(pairing["code"])["key"]

    async def unexpected_request(*args, **kwargs):
        pytest.fail("Cloudflare setup should only return a link.")

    monkeypatch.setattr(httpx.AsyncClient, "send", unexpected_request)

    result = payload(call(client, key, "connect", {"what": " Cloudflare "}))

    assert result == {"what": "cloudflare", "done": [], "next": [{
        "by": "person", "do": "Follow the Cloudflare setup guide.", "link": "https://swb.one/docs/setup/cloudflare",
    }]}
    assert not core.routes.list()
    assert not core.store.q("SELECT * FROM secrets")


def claim(client, link, token):
    path = urlparse(link).path
    page = client.get(path, headers=OWNER)
    assert page.status_code == 200 and 'type="password"' in page.text
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    response = client.post(path, data={"csrf": csrf, "token": token}, headers=OWNER)
    assert response.status_code == 200 and token not in response.text


def test_capy_claim_resume_and_restart(make, tmp_path, capy):
    env = default_env()
    del env["CAPY_API_KEY"]
    cfg = config(identity="personal")
    core, client = make(cfg, env)
    assert "Capy Settings" in core.health()["blocks"]["capy"]["next_step"]
    response = connect(client, "capy-captain")
    step = response["next"][0]
    assert response["done"] == ["Capy API key: absent."]
    assert step["by"] == "person"
    assert step["link"].rsplit("/", 1)[-1] not in step["do"]
    refused = client.get(urlparse(step["link"]).path)
    assert refused.status_code == 303 and refused.headers["location"].startswith("/login?next=%2Fclaim%2F")
    claim(client, step["link"], CAPY_KEY)
    response = connect(client, "capy-captain")
    assert response["done"] and "restart" in response["next"][0]["do"].lower()
    assert "link" not in response["next"][0]
    assert "restart" in core.health()["blocks"]["capy"]["next_step"].lower()

    async def verify_restart():
        restarted = new_core(tmp_path, capy, config=cfg, env=env)
        try:
            assert restarted.blocks["capy"].online
            assert "route" in restarted.blocks["capy"].next_step().lower()
        finally:
            await restarted.aclose()

    client.portal.call(verify_restart)


def test_capy_route_waits_for_a_service_key(make, tmp_path, capy):
    env = default_env()
    del env["CAPY_API_KEY_SERVICE"]
    cfg = config()
    core, client = make(cfg, env)
    result = connect(client, "capy-captain", choose={"captain": CAPTAIN})
    assert "state" not in result and core.routes.list("capy")[0].target["captain"] == CAPTAIN
    assert "service" in core.health()["blocks"]["capy"]["next_step"]
    claim(client, result["next"][0]["link"], SERVICE_KEY)
    assert "restart" in connect(client, "capy-captain")["next"][0]["do"].lower()

    async def verify_restart():
        restarted = new_core(tmp_path, capy, config=cfg, env=env)
        try:
            assert restarted.blocks["capy"].chain("capy") == ["CAPY_API_KEY_SERVICE"]
            assert restarted.blocks["capy"].next_step() is None
        finally:
            await restarted.aclose()

    client.portal.call(verify_restart)


def test_capy_lists_choices_and_routes_once(make, capy):
    core, client = make(config())
    result = connect(client, "capy-captain")
    assert result["done"][0] == "Capy API key: present."
    assert all(any(captain in item for item in result["done"]) for captain in (CAPTAIN, CAPTAIN2))
    step, = result["next"]
    assert step["by"] == "you" and step["call"] == {
        "tool": "connect", "arguments": {"what": "capy-captain", "choose": {"captain": "<Captain ID>"}},
    }
    assert not core.routes.list("capy")
    assert connect(client, "capy-captain", choose={"captain": CAPTAIN})["state"] == "connected"
    route, = core.routes.list("capy")
    assert not route.agent and not route.project and route.target["captain"] == CAPTAIN
    assert core.health()["blocks"]["capy"]["next_step"] is None
    assert connect(client, "capy-captain", choose={"captain": CAPTAIN})["state"] == "connected"
    assert core.routes.list("capy") == [route]
    assert connect(client, "capy-captain", choose={"captain": CAPTAIN2})["state"] == "connected"
    assert core.routes.list("capy")[0].target["captain"] == CAPTAIN2

    core.projects.create(slug="demo", name="Demo", links={"capy_project": OTHER_PROJECT})
    project = connect(client, "capy-captain", choose={"project": "demo"})
    assert any(CAPTAIN2 in item for item in project["done"])
    assert len(core.routes.list("capy")) == 1
    args = project["next"][0]["call"]["arguments"]
    args["choose"]["captain"] = CAPTAIN
    assert connect(client, **args)["state"] == "connected"
    route, _ = core.routes.resolve(destination="capy", agent="capy", project="demo")
    assert route.project == "demo" and route.target["captain"] == CAPTAIN
    assert payload(call(client, STATIC_KEY, "send", {"task": "Check the newly connected route.", "project": "demo"}))["thread_id"] == CAPTAIN
    assert any(path.endswith(f"/{CAPTAIN}/message") for _, path, _ in capy.requests)
    assert core.store.one("SELECT COUNT(*) AS n FROM audit WHERE action = 'route_set'")["n"] == 3
    assert core.store.one("SELECT COUNT(*) AS n FROM audit WHERE action = 'connect_capy-captain'")["n"] == 6


def test_claude_dedupes_resumes_and_completes(make):
    core, client = make(config())
    core.projects.create(slug="demo", name="Demo", links={"capy_project": OTHER_PROJECT})
    assert connect(client, "claude-project")["next"][0]["call"]["tool"] == "project_list"
    assert "register" in core.health()["blocks"]["claude-routine"]["next_step"]
    detail = {"claude_project": "Demo Web", "fire_url": "trig_01EXAMPLE"}
    instructions = connect(client, "claude-project", project="demo", choose={"claude_project": "Demo Web"})
    create, register = instructions["next"]
    assert create["by"] == "person" and "API trigger" in create["do"]
    assert '\n\n> Call routine_pickup(claude_project="Demo Web")' in create["do"]
    assert register["by"] == "you" and "private claim link for token entry" in register["do"]
    assert register["call"] == {
        "tool": "connect",
        "arguments": {"what": "claude-project", "choose": {
            "fire_url": "<fire URL or trigger ID>", "claude_project": "Demo Web", "project": "demo",
        }},
    }
    result = connect(client, "claude-project", choose={**detail, "project": "demo-web"})
    choice = next(s["call"]["arguments"] for s in result["next"] if "call" in s)
    assert choice["choose"]["project"] == "demo"
    assert core.blocks["claude-routine"].pending() == []

    first = connect(client, **choice)["next"][0]["link"]
    second = connect(client, "claude-project", project="demo")["next"][0]["link"]
    assert first != second
    assert client.get(urlparse(first).path, headers=OWNER).status_code == 410
    claims = core.store.q("SELECT code_hash FROM routine_claims")
    assert all(urlparse(link).path.rsplit("/", 1)[-1] != r["code_hash"] for r in claims for link in (first, second))
    assert "owner" in core.health()["blocks"]["claude-routine"]["next_step"]
    claim(client, second, TOKEN)
    result = connect(client, "claude-project", project="demo", choose=detail)
    assert result["state"] == "connected" and not result["next"]
    assert TOKEN not in json.dumps(result)
    assert TOKEN not in core.store.one("SELECT token FROM routines")["token"]
    result = connect(client, "claude-project")
    assert result["state"] == "connected" and any("token present" in item for item in result["done"])
    assert core.health()["blocks"]["claude-routine"]["next_step"] is None
    assert len(core.store.q("SELECT code_hash FROM routine_claims")) == 2


def test_configured_claude_routine_claim_takes_effect_without_restart(make):
    cfg = config()
    cfg["blocks"]["claude-routine"]["routines"] = {"claude-demo": {
        "project": "demo", "routine_id": "trig_01CONFIG", "token_env": "TEST_ROUTINE_TOKEN",
    }}
    core, client = make(cfg)
    core.projects.create(slug="demo", name="Demo", links={"capy_project": OTHER_PROJECT})
    detail = {"claude_project": "Demo Web", "fire_url": "trig_01CONFIG"}
    result = connect(client, "claude-project", project="demo", choose=detail)
    assert any("token absent" in item for item in result["done"])
    step, = result["next"]
    path = urlparse(step["link"]).path
    assert path.startswith("/claim/") and path.rsplit("/", 1)[-1] not in step["do"]
    claim(client, step["link"], TOKEN)
    result = connect(client, "claude-project", project="demo", choose=detail)
    assert result["state"] == "connected" and any("token present" in item for item in result["done"])
    block = core.blocks["claude-routine"]
    assert block.token(block.configured["claude-demo"]) == TOKEN
    assert block.next_step() is None and not block.pending()
    assert TOKEN.encode() not in core.store.one("SELECT sealed FROM secrets WHERE name = 'TEST_ROUTINE_TOKEN'")["sealed"]
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize("principal", [
    Principal("limited", "key", "key", scopes=("project:secret-project",)),
    Principal("limited", "key", "key", tools=("connect",)),
    Principal("limited", "key", "key", targets=("capy",)),
    Principal("paired", "key", "connect"),
    Principal("untrusted", "key", "key", trust="untrusted"),
])
def test_limited_principals_get_directions_only(make, capy, principal):
    core, client = make(config())
    core.projects.create(slug="secret-project", name="Private project", private=True)
    capy_block = core.blocks["capy"]
    claude = core.blocks["claude-routine"]
    before = len(capy.requests)
    for block, what, choose in (
        (capy_block, "capy-captain", None),
        (capy_block, "capy-captain", {"captain": CAPTAIN}),
        (claude, "claude-project", None),
        (claude, "claude-project", {"fire_url": "trig_01EXAMPLE", "claude_project": "Private project"}),
    ):
        out = client.portal.call(block.connect_steps, what, principal, "secret-project", choose)
        assert not out["done"] and set(out["next"][0]) == {"by", "do"}
        assert out["next"][0]["by"] == "person" and "secret-project" not in json.dumps(out)
    assert len(capy.requests) == before
    assert not core.routes.list("capy") and not claude.pending()
    assert not core.store.q("SELECT * FROM secrets")
