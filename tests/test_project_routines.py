"""Project routines: a Claude Project's API-triggered task answers for one switchboard project, a general routine for the
rest, the mailbox when neither exists. Registered from the config, the root page or routine_add."""

import json

import httpx
import yaml
from starlette.testclient import TestClient

from conftest import OWNER, PUBLIC, FakeCapy, STATIC_KEY, CIMDHost, Clock, access_verifier, base_config, call, default_env, error_text, payload, sign_in, infra
from switchboard.app import create_app
from switchboard.config import translate_v1
from switchboard.core import Switchboard

TOKEN = "sk-ant-oat01-" + "t" * 20


class Anthropic:
    def __init__(self):
        self.fired = []

    def handler(self, request):
        self.fired.append((request.url.path.split("/")[-2], json.loads(request.content)["text"]))
        return httpx.Response(200, json={"claude_code_session_id": "session_1", "claude_code_session_url": "https://claude.ai/code/session_1"})


def build(tmp_path, routines, name="c"):
    anthropic = Anthropic()
    cfg = translate_v1(base_config())
    cfg["blocks"]["claude-routine"] = {"use": "claude-routine", "routines": routines}
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(cfg))
    core = Switchboard(path, tmp_path / f"data-{name}", env={**default_env(), "GENERAL_TOKEN": TOKEN, "INFRA_TOKEN": TOKEN}, clock=Clock(),
                       oauth_http=httpx.AsyncClient(transport=httpx.MockTransport(CIMDHost().handler)), access_verifier=access_verifier,
                       destination_options={"claude-routine": {"transport": httpx.MockTransport(anthropic.handler)},
                                            "capy": {"transport": httpx.MockTransport(FakeCapy().handler)}})
    infra(core)
    core.projects.create(slug="other", name="Other")
    client = TestClient(create_app(core), base_url=PUBLIC, follow_redirects=False)
    client.__enter__()
    return core, client, anthropic


BOTH = {"claude": {"routine_id": "trig_01GENERAL", "token_env": "GENERAL_TOKEN"},
        "claude-infra": {"routine_id": "trig_01INFRA", "token_env": "INFRA_TOKEN", "project": "infra"}}


def test_project_first_then_general(tmp_path):
    core, c, anthropic = build(tmp_path, BOTH)
    try:
        assert payload(call(c, STATIC_KEY, "send", {"task": "deploy status?", "to": "claude", "project": "infra"}))["routine"] == "claude-infra"
        assert payload(call(c, STATIC_KEY, "send", {"task": "anything", "to": "claude", "project": "other"}))["routine"] == "claude"
        assert payload(call(c, STATIC_KEY, "send", {"task": "anything", "to": "claude"}))["routine"] == "claude"
        assert payload(call(c, STATIC_KEY, "send", {"task": "x", "to": "claude:infra"}))["routine"] == "claude-infra"
        assert payload(call(c, STATIC_KEY, "send", {"task": "x", "to": "claude-infra"}))["routine"] == "claude-infra"
        ids = [rid for rid, _ in anthropic.fired]
        assert ids == ["trig_01INFRA", "trig_01GENERAL", "trig_01GENERAL", "trig_01INFRA", "trig_01INFRA"]
        note = anthropic.fired[0][1]
        assert 'context_pack(project="infra")' in note and "coordinator" in note and "final=false" in note and "final=true" in note
        assert "context_pack" not in anthropic.fired[1][1]
        routine = {t["id"]: t for t in payload(call(c, STATIC_KEY, "targets", {"block": "claude-routine"}))["targets"]}
        assert routine["claude-infra"]["project"] == "infra" and "claude-infra" in routine["claude-infra"]["slugs"]
        assert core.visibility.rule("target:claude-infra") == "private"
    finally:
        c.__exit__(None, None, None)


def test_only_a_project_routine_and_the_mailbox_fallback(tmp_path):
    core, c, anthropic = build(tmp_path, {"claude-infra": BOTH["claude-infra"]})
    try:
        assert payload(call(c, STATIC_KEY, "send", {"task": "for infra", "to": "claude", "project": "infra"}))["routine"] == "claude-infra"
        plain = payload(call(c, STATIC_KEY, "send", {"task": "no routine for this", "to": "claude", "project": "other"}))
        assert plain["mailbox"] and "routine" not in plain
        assert payload(call(c, STATIC_KEY, "send", {"task": "mailbox only", "to": "agent:claude", "project": "infra"}))["mailbox"]
        assert len(anthropic.fired) == 1
    finally:
        c.__exit__(None, None, None)


def test_interim_and_final_replies_on_one_conversation(tmp_path):
    core, c, anthropic = build(tmp_path, BOTH)
    try:
        h = payload(call(c, STATIC_KEY, "send", {"task": "build the thing", "to": "claude", "project": "infra"}))["conversation"]
        tok = sign_in(c)["access_token"]
        assert payload(call(c, tok, "reply", {"conversation": h, "text": "handed to the infra coordinator", "final": False}))["final"] is False
        assert core.inbox.handle(h)["status"] == "delivered"
        assert payload(call(c, tok, "reply", {"conversation": h, "text": "built and deployed"}))["final"] is True
        assert core.inbox.handle(h)["status"] == "answered"
        got = payload(call(c, STATIC_KEY, "inbox", {"since_cursor": 0}))["items"]
        assert [(i["text"], i["final"], i["in_reply_to"]) for i in got] == [("handed to the infra coordinator", False, h), ("built and deployed", True, h)]
        res = payload(call(c, STATIC_KEY, "result", {"conversation": h}))
        assert res["final"]["text"] == "built and deployed" and [r["final"] for r in res["replies"]] == [False, True]
        assert "replies to it come from the other side" in error_text(call(c, STATIC_KEY, "reply", {"conversation": h, "text": "x"}))
    finally:
        c.__exit__(None, None, None)


def test_routes_pick_the_routine_for_a_project(tmp_path):
    core, c, anthropic = build(tmp_path, BOTH)
    try:
        assert payload(call(c, STATIC_KEY, "send", {"task": "global goes to capy"}))["block"] == "capy"
        assert payload(call(c, STATIC_KEY, "routes_set", {"target": "claude-routine:routine:claude-infra", "project": "infra"}))["level"] == "project"
        out = payload(call(c, STATIC_KEY, "send", {"task": "routed by project", "project": "infra"}))
        assert out["block"] == "claude-routine" and out["routine"] == "claude-infra"
        assert payload(call(c, STATIC_KEY, "send", {"task": "other project", "project": "other"}))["block"] == "capy"
    finally:
        c.__exit__(None, None, None)


def test_routine_tools_register_list_and_remove_without_showing_tokens(tmp_path):
    core, c, anthropic = build(tmp_path, {"claude": BOTH["claude"]})
    try:
        url = "https://api.anthropic.com/v1/claude_code/routines/trig_01NEWPROJ/fire"
        added = payload(call(c, STATIC_KEY, "routine_add", {"fire_url": url, "token": TOKEN, "project": "infra"}))
        assert added["name"] == "claude-infra" and added["status"] == "untested" and added["routine_id"] == "trig_01NEWPROJ"
        assert TOKEN not in json.dumps(payload(call(c, STATIC_KEY, "routine_list")))
        assert not core.store.q("SELECT * FROM routines WHERE token LIKE ?", (f"%{TOKEN}%",))
        assert payload(call(c, STATIC_KEY, "send", {"task": "hi", "to": "claude", "project": "infra"}))["fired"]
        assert anthropic.fired[-1][0] == "trig_01NEWPROJ"
        rows = {r["name"]: r for r in payload(call(c, STATIC_KEY, "routine_list"))["routines"]}
        assert rows["claude-infra"]["status"] == "tested" and rows["claude"]["status"] == "untested"
        assert "config file" in error_text(call(c, STATIC_KEY, "routine_remove", {"name": "claude"}))
        assert "routine token" in error_text(call(c, STATIC_KEY, "routine_add", {"fire_url": url, "token": "nope"}))
        assert "no project" in error_text(call(c, STATIC_KEY, "routine_add", {"fire_url": url, "token": TOKEN, "project": "nope"}))
        assert payload(call(c, STATIC_KEY, "routine_remove", {"name": "claude-infra"}))["removed"]
        assert "claude-infra" not in {r["name"] for r in payload(call(c, STATIC_KEY, "routine_list"))["routines"]}
        code = payload(call(c, STATIC_KEY, "connect_code", {"agent": "codex"}))["code"]
        key = c.post("/connect", json={"code": code}).json()["key"]
        assert "connected agent" in error_text(call(c, key, "routine_add", {"fire_url": url, "token": TOKEN}))
    finally:
        c.__exit__(None, None, None)


def test_root_page_adds_a_routine_and_shows_projects_across_systems(tmp_path):
    core, c, anthropic = build(tmp_path, {"claude": BOTH["claude"]})
    try:
        page = c.get("/classic", headers=OWNER)
        assert "Claude routines" in page.text and 'type="password"' in page.text and "Projects across systems" in page.text
        csrf = page.text.split('name="csrf" value="')[1].split('"')[0]
        form = {"fire_url": "https://api.anthropic.com/v1/claude_code/routines/trig_01PAGE/fire", "token": TOKEN, "project": "infra", "csrf": csrf}
        assert c.post("/routines", data={**form, "csrf": "bad"}, headers=OWNER).status_code == 403
        assert c.post("/routines", data=form, headers=OWNER).status_code == 303
        page = c.get("/classic", headers=OWNER).text
        assert "claude-infra" in page and "trig_01PAGE" in page and TOKEN not in page and "untested" in page
        systems = {s["slug"]: s["systems"] for s in core.project_systems(client_targets(c))}
        assert systems["infra"]["claude-routine routine"] == "Claude: infra" and "capy" in systems["infra"]
        assert c.post("/routines/claude-infra/remove", data={"csrf": csrf}, headers=OWNER).status_code == 303
        assert "claude-infra" not in {r["name"] for r in core.blocks["claude-routine"].rows()}
    finally:
        c.__exit__(None, None, None)


def client_targets(c):
    return payload(call(c, STATIC_KEY, "targets", {"refresh": True}))["targets"]


def test_a_target_limited_key_is_checked_after_the_project_picks_a_routine(tmp_path):
    core, c, anthropic = build(tmp_path, BOTH)
    try:
        core.cfg.key_targets["capy"] = ("claude",)
        assert "may send only to" in error_text(call(c, STATIC_KEY, "send", {"task": "x", "to": "claude", "project": "infra"}))
        assert payload(call(c, STATIC_KEY, "send", {"task": "x", "to": "claude", "project": "other"}))["routine"] == "claude"
        core.cfg.key_targets["capy"] = ("claude-infra",)
        assert payload(call(c, STATIC_KEY, "send", {"task": "x", "to": "claude-infra", "project": "infra"}))["routine"] == "claude-infra"
        assert "may send only to" in error_text(call(c, STATIC_KEY, "send", {"task": "x", "to": "claude", "project": "other"}))
        assert "not a whole block" in error_text(call(c, STATIC_KEY, "send", {"task": "x", "to": "claude-routine", "project": "infra"}))
        assert [rid for rid, _ in anthropic.fired] == ["trig_01GENERAL", "trig_01INFRA"]
    finally:
        c.__exit__(None, None, None)
