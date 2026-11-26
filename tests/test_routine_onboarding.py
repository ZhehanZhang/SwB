"""Onboarding Claude Projects: several routines on one switchboard project, self-registration over MCP with a one-time
link for the token, duplicate-project guard, and the empty-body fire for Project routines Anthropic refuses text for."""

import json

import httpx

from conftest import OWNER, STATIC_KEY, call, error_text, payload, sign_in
from test_project_routines import BOTH, TOKEN, build

URL = "https://api.anthropic.com/v1/claude_code/routines/{}/fire"
TOKEN2 = "sk-ant-oat01-" + "u" * 20


def claim(c, link):
    path = link.split("example.test", 1)[1]
    page = c.get(path, headers=OWNER)
    csrf = page.text.split('name="csrf" value="')[1].split('"')[0] if 'name="csrf"' in page.text else ""
    return path, page, csrf


def test_several_routines_share_a_project_and_the_primary_answers(tmp_path):
    core, c, anthropic = build(tmp_path, {"claude": BOTH["claude"]})
    try:
        a = payload(call(c, STATIC_KEY, "routine_add", {"fire_url": URL.format("trig_01AAA"), "token": TOKEN, "project": "infra"}))
        b = payload(call(c, STATIC_KEY, "routine_add", {"fire_url": URL.format("trig_01BBB"), "token": TOKEN2, "project": "infra"}))
        assert (a["name"], b["name"]) == ("claude-infra", "claude-infra-2") and a["primary"] and not b["primary"]
        again = payload(call(c, STATIC_KEY, "routine_add", {"fire_url": URL.format("trig_01BBB"), "token": TOKEN, "project": "infra"}))
        assert again["name"] == "claude-infra-2" and len(core.blocks["claude-routine"].rows()) == 3
        assert "already exists" in error_text(call(c, STATIC_KEY, "routine_add", {"fire_url": URL.format("trig_01CCC"), "token": TOKEN, "project": "infra",
                                                                                 "name": "claude-infra"}))
        assert payload(call(c, STATIC_KEY, "send", {"task": "x", "to": "claude", "project": "infra"}))["routine"] == "claude-infra"
        assert payload(call(c, STATIC_KEY, "send", {"task": "x", "to": "claude-infra-2"}))["routine"] == "claude-infra-2"
        assert payload(call(c, STATIC_KEY, "routine_add", {"fire_url": URL.format("trig_01BBB"), "token": TOKEN, "project": "infra", "primary": True}))["primary"]
        assert payload(call(c, STATIC_KEY, "send", {"task": "x", "to": "claude", "project": "infra"}))["routine"] == "claude-infra-2"
        assert [rid for rid, _ in anthropic.fired] == ["trig_01AAA", "trig_01BBB", "trig_01BBB"]
        long = "x" * 33
        core.projects.create(slug=long, name="Long")
        names = [payload(call(c, STATIC_KEY, "routine_add", {"fire_url": URL.format(f"trig_01L{i}"), "token": TOKEN, "project": long}))["name"] for i in range(3)]
        assert len(set(names)) == 3 and all(len(n) <= 40 for n in names)
        targets = {t["id"]: t["slugs"] for t in payload(call(c, STATIC_KEY, "targets", {"block": "claude-routine"}))["targets"]}
        assert targets["claude-infra-2"] == ["claude-infra-2"] and targets["claude-infra"] == ["claude-infra"]
        assert payload(call(c, STATIC_KEY, "send", {"task": "x", "to": "claude:infra"}))["routine"] == "claude-infra-2"
        assert payload(call(c, STATIC_KEY, "send", {"task": "x", "to": "claude-infra"}))["routine"] == "claude-infra"
        assert payload(call(c, STATIC_KEY, "routine_add", {"fire_url": URL.format("trig_01CCC"), "token": TOKEN, "project": "infra",
                                                          "name": "claude-infra", "replace": True}))["routine_id"] == "trig_01CCC"
    finally:
        c.__exit__(None, None, None)


def test_self_registration_link_takes_the_token_once(tmp_path):
    core, c, anthropic = build(tmp_path, {"claude": BOTH["claude"]})
    try:
        tok = sign_in(c)["access_token"]
        out = payload(call(c, tok, "routine_register", {"fire_url": URL.format("trig_01SELF"), "claude_project": "Personal Web Infra", "project": "infra"}))
        assert out["pending"] and out["name"] == "claude-infra" and out["link"].startswith("https://switchboard.example.test/routines/claim/")
        assert out["expires_in_s"] == 1800 and not out["creates_project"]
        assert "token" not in json.dumps({k: v for k, v in out.items() if k != "next"}).lower()
        pend = payload(call(c, STATIC_KEY, "routine_list"))
        assert [p["name"] for p in pend["pending"]] == ["claude-infra"] and "claude-infra" not in {r["name"] for r in pend["routines"]}
        path, page, csrf = claim(c, out["link"])
        assert page.status_code == 200 and "trig_01SELF" in page.text and "Personal Web Infra" in page.text and 'type="password"' in page.text
        assert c.get(path).status_code == 403
        assert c.post(path, data={"token": TOKEN, "csrf": "bad"}, headers=OWNER).status_code == 403
        assert c.post(path, data={"token": "nope", "csrf": csrf}, headers=OWNER).status_code == 400
        assert c.post(path, data={"token": TOKEN, "csrf": csrf}, headers=OWNER).status_code == 200
        assert c.post(path, data={"token": TOKEN, "csrf": csrf}, headers=OWNER).status_code == 400
        assert c.get(path, headers=OWNER).status_code == 410
        rows = {r["name"]: r for r in payload(call(c, STATIC_KEY, "routine_list"))["routines"]}
        assert rows["claude-infra"]["routine_id"] == "trig_01SELF" and rows["claude-infra"]["claude_project"] == "Personal Web Infra"
        assert rows["claude-infra"]["status"] == "untested" and not payload(call(c, STATIC_KEY, "routine_list"))["pending"]
        assert not core.store.q("SELECT * FROM routines WHERE token LIKE ?", (f"%{TOKEN}%",))
        second = payload(call(c, tok, "routine_register", {"fire_url": URL.format("trig_01TWO"), "claude_project": "Infra Ops", "project": "infra"}))
        assert second["name"] == "claude-infra-infra-ops" and not second["primary"]
        held = payload(call(c, tok, "routine_register", {"fire_url": URL.format("trig_01THREE"), "claude_project": "Infra Ops", "project": "infra"}))
        assert held["name"] != second["name"]
        bad = core.blocks["claude-routine"]
        assert "isn't valid" in error_text_of(lambda: bad.claim("nope"))
        core.clock.t += 1801
        assert c.get(claim(c, second["link"])[0], headers=OWNER).status_code == 410
    finally:
        c.__exit__(None, None, None)


def error_text_of(fn):
    try:
        fn()
    except Exception as e:
        return str(e)
    return ""


def test_registration_reads_projects_first_and_refuses_near_duplicates(tmp_path):
    core, c, anthropic = build(tmp_path, {})
    try:
        core.projects.update("other", links={"claude_project": "ExampleProject Provision"})
        tok = sign_in(c)["access_token"]
        near = payload(call(c, tok, "routine_register", {"fire_url": "trig_01DUP", "claude_project": "Infra Web", "project": "infra-web"}))
        assert not near["pending"] and near["candidates"][0]["slug"] == "infra"
        linked = payload(call(c, tok, "routine_register", {"fire_url": "trig_01LINK", "claude_project": "ExampleProject Provision"}))
        assert linked["pending"] and linked["project"] == "other"
        unknown = payload(call(c, tok, "routine_register", {"fire_url": "trig_01UNK", "claude_project": "Infra"}))
        assert not unknown["pending"] and unknown["candidates"][0]["slug"] == "infra"
        made = payload(call(c, tok, "routine_register", {"fire_url": "trig_01NEW", "claude_project": "Infra Web", "project": "infra-web",
                                                         "project_name": "Infra web", "create_new": True}))
        assert made["pending"] and made["creates_project"] and made["name"] == "claude-infra-web"
        fresh = payload(call(c, tok, "routine_register", {"fire_url": "trig_01FRESH", "claude_project": "Garden Planner", "project": "garden"}))
        assert fresh["pending"] and fresh["creates_project"]
        assert "infra-web" not in {p.slug for p in core.projects.list()}
        path, page, csrf = claim(c, made["link"])
        assert "Created when you save" in page.text
        assert c.post(path, data={"token": TOKEN, "csrf": csrf}, headers=OWNER).status_code == 200
        p = core.projects.get("infra-web")
        assert p.name == "Infra web" and p.links["claude_project"] == "Infra Web"
        assert payload(call(c, STATIC_KEY, "send", {"task": "x", "to": "claude", "project": "infra-web"}))["routine"] == "claude-infra-web"
        assert "pass claude_project" in error_text(call(c, tok, "routine_register", {"fire_url": "trig_01X", "claude_project": " ", "project": "infra"}))
        assert "fire URL" in error_text(call(c, tok, "routine_register", {"fire_url": "nope", "claude_project": "X", "project": "infra"}))
    finally:
        c.__exit__(None, None, None)


def test_project_routine_refused_with_text_is_woken_without_it(tmp_path):
    core, c, anthropic = build(tmp_path, BOTH)
    seen = []

    def paused(request):
        body = json.loads(request.content or b"{}")
        seen.append(body)
        if "text" in body:
            return httpx.Response(403, json={"error": {"message": "this project is paused", "type": "permission_error"}})
        return httpx.Response(200, json={"claude_code_session_id": "session_2", "claude_code_session_url": "https://claude.ai/code/session_2"})

    core.blocks["claude-routine"].http = httpx.AsyncClient(transport=httpx.MockTransport(paused))
    try:
        out = payload(call(c, STATIC_KEY, "send", {"task": "deploy status?", "to": "claude", "project": "infra"}))
        assert out["fired"] and out["woken_without_text"] and out["session_id"] == "session_2" and "routine_pickup" in out["note"]
        assert [set(b) for b in seen] == [{"text"}, set()]
        assert {r["name"]: r for r in core.blocks["claude-routine"].rows()}["claude-infra"]["status"] == "tested"
        tok = sign_in(c)["access_token"]
        assert payload(call(c, tok, "routine_pickup", {"routine": "claude"}))["conversation"] is None
        got = payload(call(c, tok, "routine_pickup", {"routine": "trig_01INFRA"}))
        assert got["conversation"] == out["conversation"] and got["text"].startswith("deploy status?") and 'context_pack(project="infra")' in got["text"]
        assert payload(call(c, tok, "routine_pickup", {}))["conversation"] is None
        big = "y" * 30_000
        out2 = payload(call(c, STATIC_KEY, "send", {"task": big, "to": "claude", "project": "infra"}))
        assert big in payload(call(c, tok, "routine_pickup", {}))["text"] and out2["woken_without_text"]
    finally:
        c.__exit__(None, None, None)


def test_a_claude_project_can_test_its_own_routine(tmp_path):
    core, c, anthropic = build(tmp_path, BOTH)
    try:
        tok = sign_in(c)["access_token"]
        h = payload(call(c, tok, "send", {"task": "setup test: reply pong", "to": "claude-infra", "project": "infra"}))
        assert h["fired"] and h["routine"] == "claude-infra"
        assert payload(call(c, tok, "reply", {"conversation": h["conversation"], "text": "pong"}))["final"] is True
        assert payload(call(c, tok, "result", {"conversation": h["conversation"]}))["final"]["text"] == "pong"
    finally:
        c.__exit__(None, None, None)
