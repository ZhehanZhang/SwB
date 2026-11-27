import json

import httpx

from conftest import CAPTAIN2, CAPTAIN2_NAME, OWNER, base_config, default_env
from switchboard import api_read
from switchboard.slack.avatar_files import BUNDLED_AVATARS
from test_api_read import get


UNKNOWN = "jam_UNKNOWNCAPTAIN"


def test_discovered_captain_registrant_comes_from_capy_author(make, capy):
    capy.captains.extend([
        {"id": "jam_EXAMPLEREGISTERED", "name": "Atlas", "authorId": "user_EXAMPLE", "previousAuthorId": "user_PREVIOUS"},
        {"id": "jam_EXAMPLEUNREGISTERED", "name": "Crew", "previousAuthorId": "user_PREVIOUS"},
    ])
    capy.users.extend([
        {"id": "user_EXAMPLE", "firstName": "Alex", "lastName": "Rivera", "email": "private@swb.example.com"},
        {"id": "user_PREVIOUS", "firstName": "Prior", "lastName": "Owner", "email": None},
    ])
    _, client = make()
    rows = {row["id"]: row for row in get(client, "/agents")["captains"]}
    assert rows["jam_EXAMPLEREGISTERED"]["created_by"] == "Alex Rivera"
    assert rows["jam_EXAMPLEUNREGISTERED"]["created_by"] is None
    assert "private@swb.example.com" not in json.dumps(rows)


def test_message_sender_uses_its_own_captain_avatar_not_the_conversation_target(make, monkeypatch):
    core, client = make()
    lookups = []
    original = api_read.captain_targets

    async def targets(core, destinations):
        destinations = tuple(destinations)
        lookups.append(destinations)
        return await original(core, destinations)

    monkeypatch.setattr(api_read, "captain_targets", targets)
    handle = core.inbox.open_handle(owner="codex", destination="capy", ref="jam_EXAMPLECREW", project=None,
                                    title="Example discussion", meta={}, target=f"capy:captain:{UNKNOWN}")
    for sender in (
        core.blocks["capy"]._from("jam_EXAMPLECREW", {"authorName": "Old name", "sender": {"id": CAPTAIN2, "relation": "captain"}}),
        core.blocks["capy"]._from(CAPTAIN2, {"source": "assistant"}),
        {"kind": "capy_thread", "thread_id": "jam_EXAMPLECREW", "avatar_url": "https://swb.example.com/profile?token=example"},
        {"kind": "agent", "agent": "claude-code"},
    ):
        client.portal.call(core.inbox.add, handle, "reply", {"text": "Example reply", "from": sender})

    avatar = next(row["avatar_url"] for row in get(client, "/agents")["captains"] if row["id"] == CAPTAIN2)
    detail = get(client, f"/conversations/{handle}")
    assert lookups == [("capy",)]
    first, second, crew, known = [item["from"] for item in detail["items"]]
    assert detail["conversation"]["first_sender"]["avatar_url"] == "/avatars/codex.png"
    assert first["author"] == second["author"] == CAPTAIN2_NAME
    assert first["avatar_url"] == second["avatar_url"] == avatar
    assert "sender" not in first and "avatar_url" not in crew
    assert known["avatar_url"] == "/avatars/claude-code.png"
    assert client.get(avatar).content == (BUNDLED_AVATARS / "capy.png").read_bytes()


def test_slack_approvals_project_verified_sender_names_and_same_origin_avatars(make, monkeypatch):
    config = base_config(slack={"workspaces": {"team": {"team_id": "TEXAMPLE", "identities": {"bot": {
        "token_env": "EXAMPLE_SLACK_TOKEN", "write": "approval",
    }}}}})
    core, client = make(config)
    avatar_bytes = (BUNDLED_AVATARS / "default.png").read_bytes()
    upstream = "https://avatars.slack-edge.com/example/profile.png"

    class Slack:
        async def users_info(self, *, user):
            assert user == "UEXAMPLE"
            return {"user": {"profile": {"display_name": "Alex Rivera", "real_name": "Other name",
                                         "image_192": upstream, "email": "private@swb.example.com"}}}

    def image(request):
        assert str(request.url) == upstream and "authorization" not in request.headers
        return httpx.Response(200, content=avatar_bytes)

    core.slack.runtime("team").clients["bot"] = Slack()
    monkeypatch.setattr(core, "_avatar_http", httpx.AsyncClient(transport=httpx.MockTransport(image)))
    with_user = core.slack.store.create_request(
        workspace="team", identity="bot", action="post_message", params={"text": "Example post"}, persona=None,
        key_id="dots:slack", ttl_seconds=60, requester={"user": "UEXAMPLE", "workspace": "team"},
    )
    pairing = core.connections.mint(agent="claude-code", project=None, client=None, by="owner")
    key = core.connections.redeem(pairing["code"])
    with_agent = core.slack.store.create_request(
        workspace="team", identity="bot", action="add_reaction", params={"name": "wave"}, persona={"name": "Another label"},
        key_id=key["key_id"], ttl_seconds=60,
    )
    rows = {row["id"]: row for row in get(client, "/inbox")["pending_writes"]}
    sender = rows[with_user["id"]]["sender"]
    assert sender["name"] == "Alex Rivera" and sender["avatar_url"].startswith("/avatars/slack-profile-")
    assert client.get(sender["avatar_url"], headers=OWNER).content == avatar_bytes
    assert client.get(sender["avatar_url"]).status_code in (401, 403)
    assert rows[with_agent["id"]]["sender"] == {
        "kind": "agent", "agent": "claude-code", "name": "Claude Code", "avatar_url": "/avatars/claude-code.png",
    }
    assert rows[with_agent["id"]]["key_id"] == key["key_id"]
    assert all(value not in json.dumps(rows) for value in (upstream, "private@swb.example.com", key["key"], pairing["code"]))


def test_audit_labels_capy_references_but_preserves_event_identities(make):
    core, client = make(base_config(), env=default_env())
    address = f"capy:captain:{CAPTAIN2}"
    for action, target, ref, excerpt in (
        ("route_set", "github/context", None, json.dumps({"target_name": address, "repo_count": 1})),
        ("route_set", "*/*", None, core.routes.set(destination="capy", agent=None, project=None,
                                                  target={"captain": UNKNOWN}, defaults=None,
                                                  allow_override=False, note="", by="owner").audit_excerpt()),
        ("delegate", f"captain Previous name ({CAPTAIN2})", "jam_CREW", ""),
        ("reply", CAPTAIN2, CAPTAIN2, ""),
        ("cancel", f"capy:captain:{UNKNOWN}", None, ""),
    ):
        core.audit.record(origin="mcp", source="codex", client="key:codex", destination="capy",
                          action=action, outcome="ok", target=target, ref=ref, excerpt=excerpt)

    events = {row["target"]: row for row in get(client, "/audit")["items"]}
    avatar = events["github/context"]["metadata"]["target_avatar_url"]
    assert avatar.startswith("/avatars/") and client.get(avatar).headers["content-type"] == "image/png"
    assert events["github/context"]["metadata"] == {
        "agent": "github", "target_name": CAPTAIN2_NAME, "repo_count": 1, "target_avatar_url": avatar,
    }
    assert events["*/*"]["metadata"]["target_name"] == "Captain"
    assert events[f"captain Previous name ({CAPTAIN2})"]["metadata"] == {
        "target_label": CAPTAIN2_NAME, "ref_label": "Capy thread", "target_avatar_url": avatar,
    }
    assert events[CAPTAIN2]["metadata"] == {
        "target_label": CAPTAIN2_NAME, "ref_label": CAPTAIN2_NAME, "target_avatar_url": avatar,
    }
    assert events[CAPTAIN2]["ref"] == CAPTAIN2
    assert events[f"capy:captain:{UNKNOWN}"]["metadata"]["target_label"] == "Captain"

    selected = get(client, "/agents/detail", kind="captain", id=CAPTAIN2, block="capy")
    [reply] = [row for row in selected["audit"] if row["action"] == "reply"]
    assert reply["metadata"]["target_label"] == CAPTAIN2_NAME


def test_capy_targets_use_the_same_display_labels_across_board_and_inbox(make, capy):
    capy.captains.append({"id": UNKNOWN})
    core, client = make(base_config(), env=default_env())
    core.projects.create(slug="context", name="Context")
    for agent, captain in (("codex", CAPTAIN2), ("gemini", UNKNOWN)):
        core.routes.set(destination="capy", agent=agent, project="context", target={"captain": captain, "name": captain},
                        defaults=None, allow_override=False, note="", by="owner")
        core.inbox.open_handle(owner=agent, destination="capy", ref=f"jam_CREW_{agent}", project="context",
                               title="A delegation", meta={}, target=f"capy:captain:{captain}")
    expected = {f"capy:captain:{CAPTAIN2}": CAPTAIN2_NAME, f"capy:captain:{UNKNOWN}": "Captain"}
    board = get(client, "/board")["projects"][0]
    assert {row["id"]: row["label"] for row in board["agents"]} == expected
    project = get(client, "/board/projects/context")
    assert {row["to"]["id"]: row["to"]["label"] for row in project["routes"]} == expected
    assert {row["endpoint"]["id"]: row["endpoint"]["label"] for row in project["conversations"]} == expected
    route_targets = [row["target"] for row in get(client, "/routes")["routes"] if row["project"] == "context"]
    assert {f"capy:captain:{target['id']}": target["name"] for target in route_targets} == expected

    inbox = get(client, "/inbox")["conversations"]
    assert {row["target"]: row["first_target"] for row in inbox} == expected
    for row in inbox:
        detail = get(client, f"/conversations/{row['handle']}")["conversation"]
        assert detail["target"] == row["target"] and detail["ref"] == row["ref"]
        assert detail["first_target"] == expected[row["target"]]
