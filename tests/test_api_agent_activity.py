from conftest import CAPTAIN, CAPTAIN2, OWNER, base_config
from switchboard.audit import iso


def test_agents_only_show_recorded_captain_and_dot_activity(make, capy):
    capy.captains[0].update(createdAt="2026-10-07T09:00:00Z", updatedAt="2026-11-25T10:00:00Z",
                            lastActivityAt="2026-11-26T11:00:00Z", authorId="user_capture_not_a_display_name")
    capy.captains[1].update(createdAt="invalid timestamp", updatedAt="2026-11-18T10:00:00Z", lastActivityAt=None)
    config = base_config(version=2, blocks={"mcp": {}, "capy": {}, "dots": {"workspace": "team"}})
    core, client = make(config)
    for ident, name in (("UACTIVEDOT", "Active"), ("UIDLEDOT", "Idle")):
        core.store.x("INSERT INTO dots(workspace, user_id, name, slug, seen_at) VALUES (?,?,?,?,?)",
                     ("team", ident, name, name.lower(), core.clock()))
    for channel, dot, sent, seen in (
        ("C1", "UACTIVEDOT", 1_750_000_000, 1_750_000_030),
        ("C2", "UACTIVEDOT", 1_752_000_000, 1_752_000_000),
        ("C3", "UIDLEDOT", 1_752_000_100, 1_752_000_100),
    ):
        core.store.x("INSERT INTO dot_threads(workspace, channel, thread_ts, handle, dot, last_ts) VALUES (?,?,?,?,?,?)",
                     ("team", channel, str(sent), f"h_{channel}", dot, str(seen)))

    response = client.get("/api/agents", headers=OWNER)
    assert response.status_code == 200
    payload = response.json()
    assert "user_capture_not_a_display_name" not in response.text
    captains = {row["id"]: row for row in payload["captains"]}
    assert captains[CAPTAIN]["created_at"] == "2026-10-07T09:00:00Z"
    assert captains[CAPTAIN]["last_seen_at"] == "2026-11-26T11:00:00Z"
    assert captains[CAPTAIN2]["created_at"] is None
    assert captains[CAPTAIN2]["last_seen_at"] == "2026-11-18T10:00:00Z"
    dots = {row["name"]: row for row in payload["dots"]}
    assert dots["Active"]["last_seen_at"] == iso(1_750_000_030)
    assert dots["Idle"]["last_seen_at"] is None
