from conftest import CAPTAIN, OWNER, SECOND_KEY, STATIC_KEY, call, error_text, payload, sign_in, beacon


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
    row = core.audit.recent(action="send")[0]
    assert row["action"] == "send"
    assert row["source"] == "capy" and row["target"] == "claude"


def test_gemini_redirect_is_allowed_and_signs_in_as_gemini(make):
    core, c = make()
    gem = "https://oauth-redirect.googleusercontent.com/r/some-project"
    tok = sign_in(c, redirect=gem)["access_token"]
    assert payload(call(c, tok, "switchboard_status"))["you"]["agent"] == "gemini"


def test_conversation_filter_follows_mailbox_reply_handles(make):
    _, c = make()
    tok = sign_in(c)["access_token"]
    root = payload(call(c, STATIC_KEY, "send", {"to": "agent:claude", "task": "Check the build"}))["conversation"]
    interim = payload(call(c, tok, "reply", {"conversation": root, "text": "Checking", "final": False}))
    final = payload(call(c, tok, "reply", {"conversation": root, "text": "Passed"}))
    followup = payload(call(c, STATIC_KEY, "reply", {"conversation": interim["conversation"], "text": "Also check staging"}))
    nested = payload(call(c, tok, "reply", {"conversation": followup["conversation"], "text": "Staging passed"}))
    payload(call(c, tok, "send", {"to": "agent:capy", "task": "Unrelated"}))

    args = {"conversation": root, "timeout_s": 0}
    got = payload(call(c, STATIC_KEY, "wait", args))
    assert not got["timed_out"] and got["handle_status"] == "answered"
    assert [item["handle"] for item in got["items"]] == [interim["conversation"], final["conversation"], nested["conversation"]]
    assert [item["in_reply_to"] for item in got["items"]] == [root, root, followup["conversation"]]
    assert [item["final"] for item in got["items"]] == [False, True, True]
    assert payload(call(c, STATIC_KEY, "wait", args))["timed_out"]
    unfiltered = payload(call(c, STATIC_KEY, "inbox"))
    assert [item["text"] for item in unfiltered["items"]] == ["Unrelated"]
    inbox = payload(call(c, STATIC_KEY, "inbox", {"conversation": root, "since_cursor": 0}))
    assert inbox["items"] == got["items"]


def test_mailbox_cancel_stops_delivery_and_following(make):
    core, client = make()
    handle = payload(call(client, STATIC_KEY, "send", {"to": "agent:second", "task": "Check the build"}))["conversation"]
    args = {"conversation": handle}
    payload(call(client, SECOND_KEY, "reply", {**args, "text": "Checking", "final": False}))
    for _ in range(2):
        assert payload(call(client, STATIC_KEY, "cancel", args)) == {**args, "status": "cancelled"}
    late = payload(call(client, SECOND_KEY, "reply", {**args, "text": "Passed"}))
    assert late["status"] == "cancelled" and late["delivered"] is False
    result = payload(call(client, STATIC_KEY, "result", args))
    assert result["status"] == "cancelled" and result["final"] is None
    assert [reply["text"] for reply in result["replies"]] == ["Checking"]
    wait = payload(call(client, STATIC_KEY, "wait", {**args, "timeout_s": 10}))
    assert wait["handle_status"] == "cancelled" and not wait["timed_out"] and wait["items"] == []
    inbox = payload(call(client, STATIC_KEY, "inbox", {**args, "since_cursor": 0}))
    assert inbox["handle_status"] == "cancelled" and inbox["items"] == []
    assert [item["text"] for item in payload(call(client, STATIC_KEY, "inbox"))["items"]] == ["Checking"]
    history = client.get(f"/api/conversations/{handle}", headers=OWNER).json()
    assert [item["text"] for item in history["items"]] == ["Check the build", "Checking"]
    assert len(core.audit.recent(action="cancel", outcome="ok")) == 1


def test_mailbox_wait_and_inbox_share_consumption_in_both_directions(make):
    _, client = make()
    handle = payload(call(client, STATIC_KEY, "send", {"to": "agent:second", "task": "Check the build"}))["conversation"]
    first = {"conversation": handle, "text": "Checking", "final": False}
    payload(call(client, SECOND_KEY, "reply", first))
    unfiltered = payload(call(client, STATIC_KEY, "wait", {"timeout_s": 0}))["items"]
    assert [item["text"] for item in unfiltered] == ["Checking"]
    payload(call(client, SECOND_KEY, "reply", {"conversation": handle, "text": "Passed"}))
    filtered = payload(call(client, STATIC_KEY, "wait", {"conversation": handle, "timeout_s": 0}))["items"]
    assert [item["text"] for item in filtered] == ["Passed"]
    assert payload(call(client, STATIC_KEY, "inbox"))["items"] == []
    assert payload(call(client, STATIC_KEY, "inbox", {"conversation": handle, "since_cursor": 0}))["items"] == unfiltered + filtered
