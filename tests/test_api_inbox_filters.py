import json

import pytest

from conftest import OWNER, PUBLIC, Clock


def _seed(core):
    for handle, owner, destination, title, target, project, status, kind, meta, data in (
        ("h_alpha", "claude-code", "slack", "Developers", "agent:chatgpt", "team", "sent", "message",
         {"from": {"kind": "slack", "user_name": "Jordan", "channel_name": "dev"}}, {"text": "Review the quarterly roadmap."}),
        ("h_beta", "owner", "mailbox", "Watch digest: team/repo", "agent:owner", "team", "delivered", "watch_digest",
         {}, {"count": 2, "block": "github", "space": "team/repo", "items": []}),
        ("h_charlie", "grok", "mailbox", "News", "agent:claude", None, "answered", "message", {}, {"text": "April update."}),
    ):
        core.inbox.open_handle(handle=handle, owner=owner, destination=destination, ref=target, project=project,
                               title=title, status=status, meta=meta, target=target)
        core.store.x("INSERT INTO inbox(owner, handle, destination, kind, data, at) VALUES (?, ?, ?, ?, ?, ?)",
                     (owner, handle, destination, kind, json.dumps(data), core.clock()))


def _query(client, expect_status=200, **params):
    result = client.get("/api/inbox", params=params, headers=OWNER)
    assert result.status_code == expect_status
    return result.json()


@pytest.mark.parametrize(("filters", "expected"), [
    ({"sender": "jordan"}, ["h_alpha"]),
    ({"sender": "Claude Code"}, ["h_alpha"]),
    ({"sender": "GitHub watch"}, ["h_beta"]),
    ({"recipient": "ChatGPT"}, ["h_alpha"]),
    ({"channel": "#dev"}, ["h_alpha"]),
    ({"block": "github"}, ["h_beta"]),
    ({"kind": "watch_digest"}, ["h_beta"]),
    ({"status": "stored"}, ["h_beta"]),
    ({"q": "quarterly roadmap"}, ["h_alpha"]),
    ({"q": "quarterly roadmap", "project": "other"}, []),
    ({"recipient": "%"}, []),
    ({"project": "team", "sort": "newest"}, ["h_beta", "h_alpha"]),
])
def test_filters_use_saved_message_fields(make, filters, expected):
    core, client = make(clock=Clock())
    _seed(core)
    assert [row["handle"] for row in _query(client, **filters)["conversations"]] == expected


@pytest.mark.parametrize(("sort", "expected"), [
    ("oldest", ["h_alpha", "h_beta", "h_charlie"]),
    ("newest", ["h_charlie", "h_beta", "h_alpha"]),
])
def test_filtered_inbox_paginates_at_the_same_timestamp(make, sort, expected):
    core, client = make(clock=Clock())
    _seed(core)
    rows = []
    query = {"sort": sort, "limit": 1}
    while True:
        page = _query(client, **query)
        rows.extend(row["handle"] for row in page["conversations"])
        if not page["next_cursor"]:
            break
        assert len(rows) <= len(expected)
        query["cursor"] = page["next_cursor"]
    assert rows == expected
    assert _query(client, sort="bad", expect_status=400)["error"] == "invalid sort"
    assert _query(client, unread="yes", expect_status=400)["error"] == "invalid unread"


def test_owner_read_receipt_is_csrf_protected_and_matches_digest_version(make):
    core, client = make(clock=Clock())
    _seed(core)
    cursor = core.store.one("SELECT id FROM inbox WHERE handle = ?", ("h_beta",))["id"]
    url = "/api/inbox/actions"
    body = {"action": "read", "handles": ["h_beta"], "cursor": cursor, "count": 2}
    assert client.post(url, json=body, headers={"Origin": PUBLIC}).status_code == 401
    assert client.post(url, json=body, headers=OWNER).status_code == 403
    session = client.get("/auth/session", headers=OWNER).json()
    headers = {**OWNER, "Origin": PUBLIC, "X-CSRF-Token": session["csrf"]}
    core.store.x("UPDATE inbox SET data = json_set(data, '$.count', 3) WHERE id = ?", (cursor,))
    assert client.post(url, json=body, headers=headers).json() == {"updated": []}
    assert [row["handle"] for row in _query(client, unread="1", kind="watch_digest")["conversations"]] == ["h_beta"]
    assert client.post(url, json={**body, "count": 3}, headers=headers).json() == {"updated": ["h_beta"]}
    assert _query(client, unread="1", kind="watch_digest")["conversations"] == []
    assert [row["handle"] for row in _query(client, unread="0")["conversations"]] == ["h_beta"]
    assert client.post(url, json={"action": "read", "handles": ["h_alpha", "h_charlie"]}, headers=headers).json() == {
        "updated": ["h_alpha", "h_charlie"],
    }
    assert _query(client, unread="1")["conversations"] == []
    item = core.store.one("SELECT data FROM inbox WHERE id = ?", (cursor,))
    assert json.loads(item["data"])["count"] == 3


def test_redelivery_uses_the_owner_mailbox_notifier(make):
    core, client = make(clock=Clock())
    _seed(core)
    session = client.get("/auth/session", headers=OWNER).json()
    headers = {**OWNER, "Origin": PUBLIC, "X-CSRF-Token": session["csrf"]}
    body = {"action": "redeliver", "handles": ["h_beta", "h_alpha"]}
    assert client.post("/api/inbox/actions", json=body, headers={**OWNER, "Origin": PUBLIC}).status_code == 403
    response = client.post("/api/inbox/actions", json=body, headers=headers)
    assert response.status_code == 200 and response.json() == {"updated": ["h_beta"]}
    assert core.inbox.handle("h_beta")["status"] == "no_owner_surface"
    assert core.inbox.handle("h_alpha")["status"] == "sent"
