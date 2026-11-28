import json

import httpx
import pytest

from conftest import CAPTAIN, CAPTAIN2, CAPTAIN2_NAME, OWNER, STATIC_KEY, base_config, call, default_env, error_text
from switchboard.blocks.discord import Channel
from switchboard import api_read
from switchboard.slack.avatar_files import BUNDLED_AVATARS
from switchboard.targets import CaptainTargetError
from switchboard.trust import frame_sender
from test_api_read import get
from test_api_write import owner_headers


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


def test_watch_sender_and_owner_delivery_metadata_are_projected_without_a_read_receipt(make):
    core, client = make()
    repo = "example-org/example-repo"
    legacy = core.inbox.open_handle(owner="owner", destination="mailbox", ref="", project="example",
                                    title=f"Watch digest for {repo}", meta={"owner_archived": True}, status="delivered")
    routed = core.inbox.open_handle(owner="claude", destination="mailbox", ref="", project="example",
                                    title=f"Watch digest for {repo}", meta={"for_owner": True, "resolved_by": "project"},
                                    status="delivered", target="agent:claude")
    cursor = client.portal.call(core.inbox.add, routed, "watch_digest", {
        "block": "github", "space": repo, "channel": repo, "count": 1,
        "items": [{"author": "Example user", "excerpt": "Example event", "link": None, "at": 1}],
    })
    sent = core.inbox.open_handle(owner="claude", destination="mailbox", ref="unknown-key", project=None, title=None,
                                  meta={}, target="agent:claude", status="delivered")
    by_handle = {row["handle"]: row for row in get(client, "/inbox")["conversations"]}
    assert legacy not in by_handle
    assert client.get(f"/api/conversations/{legacy}", headers=OWNER).status_code == 404
    watch = by_handle[routed]
    assert watch["status"] == "delivered"
    assert watch["target"] == "agent:claude" and watch["first_target"] == "Claude"
    assert watch["for_owner"] is True and watch["resolved_by"] == "project"
    assert watch["first_sender"]["name"] == "GitHub watch" and watch["first_sender"]["kind"] == "watch"
    assert (watch["source_kind"], watch["channel_label"], watch["project"]) == ("github", repo, "example")
    detail = get(client, f"/conversations/{routed}")
    assert detail["conversation"] == watch and detail["cursor"] == cursor
    assert detail["items"][0]["from"]["name"] == "GitHub watch"
    assert not core.store.q("SELECT 1 FROM inbox_cursors")
    assert by_handle[sent]["first_sender"]["name"] == "unknown-key"


def test_slack_provenance_and_dot_identity_win_over_transport_identity(make):
    core, client = make()
    handle = core.inbox.open_handle(owner="slack", destination="dots", ref="UDEST", project=None,
                                    title="dot example-dot", target="dots:dot:UDEST", meta={
                                        "dot": "example-dot", "from": {"kind": "agent", "agent": "slack"},
                                        "display_from": {"kind": "slack", "workspace": "team", "channel": "CDEV",
                                                         "channel_name": "dev", "user_name": "Alex Rivera"},
                                        "channel_mode": {"block": "slack", "space": "team", "channel": "CDEV", "project": "example"},
                                    })
    trust, text = frame_sender("# **Fix the build**\nSecond line", "Alex Rivera", "slack:user")
    client.portal.call(core.inbox.add, handle, "message", {"text": text, "trust": trust})
    row = get(client, f"/conversations/{handle}")["conversation"]
    assert row["first_sender"]["name"] == "Alex Rivera" and row["first_sender"]["kind"] == "slack"
    assert row["project"] == "example" and row["channel_label"] == "#dev"
    assert row["target"] == "dots:dot:UDEST" and row["first_target"] == "example-dot"
    assert row["target_kind"] == "dot" and row["target_avatar_url"] == "/avatars/dots.png"
    assert row["title"] == "Fix the build"


def test_legacy_channel_titles_use_first_item_even_on_later_detail_pages(make):
    core, client = make()
    handle = core.inbox.open_handle(owner="codex", destination="telegram", ref="home:100:20", project=None,
                                    title="Telegram home", meta={"space": "home"})
    cursor = client.portal.call(core.inbox.add, handle, "telegram_event", {
        "text": "**Fix the release**\nThis is not the title", "subject": "Telegram home",
        "from": {"kind": "telegram", "space": "home", "channel": "100", "topic": 7, "channel_name": "Home"},
    })
    client.portal.call(core.inbox.add, handle, "telegram_event", {"subject": "A later message"})
    row = get(client, f"/conversations/{handle}", cursor=cursor)["conversation"]
    assert (row["title"], row["first_target"], row["target_avatar_url"]) == ("Fix the release", "Codex", "/avatars/codex.png")
    assert row["channel"] == "100:7" and row["channel_label"] == "Home › Topic 7"


def test_channel_addresses_render_labels_and_telegram_uses_stored_topic_and_route(make):
    config = base_config(version=2, blocks={
        "mcp": {}, "telegram": {"connect": False, "spaces": {"home": {"chats": [-1001], "project": "team"}}},
        "discord": {"connect": False, "spaces": {"work": {"guild_id": "3000"}}}, "github": {}, "slack": {},
    })
    core, client = make(config=config)
    telegram = core.blocks["telegram"]
    chat = {"id": -1001, "title": "Team Dispatch", "type": "supergroup"}
    client.portal.call(telegram._receive, {"chat": chat, "message_id": 5, "forum_topic_created": {"name": "Design"}}, 2, "fixture_bot")
    client.portal.call(telegram._receive, {
        "chat": chat, "message_id": 8, "message_thread_id": 5, "forum_topic_edited": {"name": "Design Review"},
    }, 2, "fixture_bot")
    core.blocks["discord"].channels.update({
        "2001": Channel(None, "development", 0, "3000"),
        "2002": Channel("2001", "releases", 11, "3000"),
    })
    examples = (
        ("telegram", "home:-1001:5", {}, "home › Design Review"),
        ("telegram", "home:-1001:9", {}, "home › Topic 9"),
        ("discord", "work:2001:2002", {}, "#development › releases"),
        ("slack", "studio:CEXAMPLE", {"from": {"kind": "slack", "workspace": "studio", "channel": "CEXAMPLE",
                                               "channel_name": "announcements"}}, "#announcements"),
        ("github", "github:issue:example/repo:42", {}, "example/repo#42"),
    )
    for block, address, meta, label in examples:
        handle = core.inbox.open_handle(owner="ChatGPT", destination=block, ref=address, project=None, title="",
                                        meta=meta, target=address)
        row = get(client, f"/conversations/{handle}")["conversation"]
        assert row["first_target"] == label and row["target"] == address
        assert row["project"] == ("team" if block == "telegram" else None)
        if block == "telegram":
            assert row["first_sender"]["name"] == "ChatGPT"
    assert len(get(client, "/inbox", project="team")["conversations"]) == 2


def test_bare_captain_slug_collision_is_not_a_canonical_recipient(make, capy):
    capy.captains.append({"id": "jam_ZZEXAMPLE", "name": "Captain", "code": "EXAMPLE-2"})
    core, client = make()
    targets = client.portal.call(core.targets)
    slugs = core.slug_map(targets)
    assert all("captain" not in target["slugs"] for target in targets)
    assert "captain" not in slugs
    assert "captain" not in core.slug_map(targets, {"captain": "example-2"})
    with pytest.raises(CaptainTargetError, match="Available Captains"):
        client.portal.call(core.resolve_to, "captain")
    task = "Check the example"
    for tool, args in (
        ("send", {"to": "captain", "task": task}),
        ("capy_delegate", {"target": "captain", "task": task}),
        ("routes_set", {"target": "captain"}),
    ):
        error = error_text(call(client, STATIC_KEY, tool, args))
        assert all(name in error for name in ("Captain", CAPTAIN2_NAME, "orbit-3", "example-2"))
    assert not core.inbox.handles()
    assert client.portal.call(core.resolve_to, f"capy:captain:{CAPTAIN2}").id == CAPTAIN2
    core.cfg.targets["aliases"] = {"review-captain": "example-2"}
    assert client.portal.call(core.resolve_to, "review-captain").id == "jam_ZZEXAMPLE"
    assert client.portal.call(core.blocks["capy"].resolve_captain, "review-captain")["id"] == "jam_ZZEXAMPLE"

    legacy = core.inbox.open_handle(owner="capy", destination="slack", ref="team", project=None, title=None, meta={})
    client.portal.call(core.inbox.add, legacy, "slack_event", {"target": "captain", "target_kind": "captain"})
    picked = core.inbox.open_handle(owner="slack", destination="capy", ref="jam_THREAD", project=None, title=None, meta={},
                                    target=f"capy:captain:{CAPTAIN2}")
    generic = get(client, f"/conversations/{legacy}")["conversation"]
    resolved = get(client, f"/conversations/{picked}")["conversation"]
    assert (generic["first_target"], generic["target_kind"], generic["target_avatar_url"]) == ("Captain", "captain", "/avatars/capy.png")
    assert resolved["first_target"] == CAPTAIN2_NAME and resolved["target"] == f"capy:captain:{CAPTAIN2}"


def test_bare_captain_resolves_the_only_active_captain_regardless_of_name(make, capy):
    capy.captains[:] = [c for c in capy.captains if c["id"] == CAPTAIN2] + [
        {"id": CAPTAIN, "name": "Captain", "archived": True},
    ]
    core, client = make()
    assert client.portal.call(core.resolve_to, "Captain").id == CAPTAIN2
    response = client.post("/api/send", headers=owner_headers(client),
                           json={"to": "captain", "task": "Review the example", "dry_run": True})
    assert response.status_code == 200
    assert response.json()["to"] == f"capy:captain:{CAPTAIN2}"
    capy.captains.append({"id": CAPTAIN, "name": "Captain"})
    with pytest.raises(CaptainTargetError, match="Available Captains"):
        client.portal.call(core.resolve_to, "Captain")


def test_bare_captain_requires_an_active_captain(make, capy):
    capy.captains[:] = [{"id": CAPTAIN, "name": "Captain", "archived": True}]
    core, client = make()
    with pytest.raises(CaptainTargetError, match="Connect a Captain"):
        client.portal.call(core.resolve_to, "captain")
