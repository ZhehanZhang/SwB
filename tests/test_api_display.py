import json

from conftest import CAPTAIN2, base_config, default_env
from test_api_read import get


UNKNOWN = "jam_UNKNOWNCAPTAIN"


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
        "agent": "github", "target_name": "Orbit Captain", "repo_count": 1, "target_avatar_url": avatar,
    }
    assert events["*/*"]["metadata"]["target_name"] == "Captain"
    assert events[f"captain Previous name ({CAPTAIN2})"]["metadata"] == {
        "target_label": "Orbit Captain", "ref_label": "Capy thread", "target_avatar_url": avatar,
    }
    assert events[CAPTAIN2]["metadata"] == {
        "target_label": "Orbit Captain", "ref_label": "Orbit Captain", "target_avatar_url": avatar,
    }
    assert events[CAPTAIN2]["ref"] == CAPTAIN2
    assert events[f"capy:captain:{UNKNOWN}"]["metadata"]["target_label"] == "Captain"

    selected = get(client, "/agents/detail", kind="captain", id=CAPTAIN2, block="capy")
    [reply] = [row for row in selected["audit"] if row["action"] == "reply"]
    assert reply["metadata"]["target_label"] == "Orbit Captain"


def test_capy_targets_use_the_same_display_labels_across_board_and_inbox(make, capy):
    capy.captains.append({"id": UNKNOWN})
    core, client = make(base_config(), env=default_env())
    core.projects.create(slug="context", name="Context")
    for agent, captain in (("codex", CAPTAIN2), ("gemini", UNKNOWN)):
        core.routes.set(destination="capy", agent=agent, project="context", target={"captain": captain, "name": captain},
                        defaults=None, allow_override=False, note="", by="owner")
        core.inbox.open_handle(owner=agent, destination="capy", ref=f"jam_CREW_{agent}", project="context",
                               title="A delegation", meta={}, target=f"capy:captain:{captain}")
    expected = {f"capy:captain:{CAPTAIN2}": "Orbit Captain", f"capy:captain:{UNKNOWN}": "Captain"}
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
