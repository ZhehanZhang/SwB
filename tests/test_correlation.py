from conftest import CAPTAIN, STATIC_KEY, call, error_text, payload, sign_in, beacon


def test_no_handle_inbox_returns_only_the_callers_items_and_handles_never_cross(make, capy):
    core, c = make()
    beacon(core)
    tok = sign_in(c)["access_token"]
    mine = payload(call(c, tok, "capy_delegate", {"task": "claude task", "project": "beacon"}))
    theirs = payload(call(c, STATIC_KEY, "capy_delegate", {"task": "capy task"}))
    import asyncio
    asyncio.run(core.inbox.add(mine["handle"], "reply", {"text": "for claude"}))
    asyncio.run(core.inbox.add(theirs["handle"], "reply", {"text": "for capy"}))
    assert [i["text"] for i in payload(call(c, tok, "capy_inbox"))["items"]] == ["for claude"]
    assert [i["text"] for i in payload(call(c, tok, "capy_inbox", {"since_cursor": 0, "project": "beacon"}))["items"]] == ["for claude"]
    assert payload(call(c, tok, "capy_inbox", {"since_cursor": 0, "project": "other"}))["items"] == []
    stolen = payload(call(c, tok, "capy_wait", {"handle": theirs["handle"], "timeout_s": 0}))
    assert stolen["items"] == [] and stolen["handle_status"] == "unknown"
    assert "no handle" in error_text(call(c, tok, "capy_reply", {"thread_id": theirs["handle"], "text": "hijack"}))
    assert "no handle" in error_text(call(c, tok, "capy_status", {"thread_id": theirs["handle"]}))
    assert "no Capy handle" in error_text(call(c, tok, "capy_cancel", {"handle": theirs["handle"]}))
    assert [i["text"] for i in payload(call(c, STATIC_KEY, "capy_wait", {"timeout_s": 0}))["items"]] == ["for capy"]


def test_agent_send_lands_in_the_named_agents_mailbox_attributed_to_the_caller(make):
    core, c = make()
    beacon(core)
    tok = sign_in(c)["access_token"]
    out = payload(call(c, STATIC_KEY, "agent_send", {"to": "claude", "text": "From: chatgpt\nPlease review the plan", "project": "beacon"}))
    assert out["delivered"] and out["to"] == "claude"
    got = payload(call(c, tok, "capy_wait", {"timeout_s": 0}))["items"]
    assert len(got) == 1 and got[0]["kind"] == "message" and got[0]["text"].endswith("Please review the plan")
    assert got[0]["from"]["agent"] == "capy" and got[0]["from"]["label"] == "Capy" and got[0]["from"]["auth"] == "key"
    assert payload(call(c, STATIC_KEY, "capy_inbox", {"since_cursor": 0}))["items"] == []
    assert "no agent" in error_text(call(c, STATIC_KEY, "agent_send", {"to": "nobody", "text": "x"}))
    assert "that is you" in error_text(call(c, STATIC_KEY, "agent_send", {"to": "capy", "text": "x"}))
    row = core.audit.recent(action="agent_send")[0]
    assert row["source"] == "capy" and row["target"] == "claude"


def test_gemini_redirect_is_allowed_and_signs_in_as_gemini(make):
    core, c = make()
    gem = "https://oauth-redirect.googleusercontent.com/r/some-project"
    tok = sign_in(c, redirect=gem)["access_token"]
    assert payload(call(c, tok, "switchboard_status"))["you"]["agent"] == "gemini"
