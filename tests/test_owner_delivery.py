import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
import yaml
from conftest import Clock, STATIC_KEY, acall, default_env, principal
from httpx import ASGITransport, AsyncClient

from switchboard.app import create_app
from switchboard.auth import sha
from switchboard.core import CoreError, Switchboard
from switchboard.owner_delivery import OWNER_ACTIVE_WINDOW_S
from switchboard.trust import CLOSE, OPEN

REPO = "example-org/app"
PUBLIC_URL = "https://office.example.com"


@pytest.fixture
async def instance(tmp_path):
    instances = []
    clock = Clock()

    def open_instance(*, settings=None):
        path = tmp_path / "switchboard.yaml"
        if settings is not None or not path.exists():
            path.write_text(yaml.safe_dump({
                "version": 2,
                "public_url": PUBLIC_URL,
                "modes": {"digest_minutes": 1},
                "blocks": {"mcp": {}, "telegram": {"spaces": {"home": {"owner_ids": ["101"]}}}},
                **(settings or {}),
            }), encoding="utf-8")
        core = Switchboard(path, tmp_path / "data", env=default_env(), clock=clock, slack_options={"connect": False})
        instances.append(core)
        return core

    async def close(core):
        await core.aclose()
        instances.remove(core)

    yield open_instance, close
    for core in instances:
        await core.aclose()


def route(core, target, *, project=None, agent=None):
    core.routes.set(destination="agent", agent=agent, project=project, target={"agent": target}, defaults=None,
                    allow_override=True, note="", by="owner")


def watch(core, start, count, *, target=None, dm=False, project=None, block="github", space=REPO):
    for i in range(start, start + count):
        core.modes.watch(block, space, "channel", str(i), "321", "Test author", f"Test excerpt {i}",
                         None, target, dm=dm, project=project)


def items(core, owner=None, *, advance=False, since=0):
    return core.inbox.read(owner=owner, since=since, advance=advance)["items"]


def oauth(core):
    client = "test-oauth-client"
    family = "test-oauth-family"
    token = "synthetic-access-token"
    core.store.x("""INSERT INTO oauth_clients(client_id, redirect_uris, auth_method, kind, created_at)
                  VALUES (?, '[]', 'none', 'dcr', ?)""", (client, core.clock()))
    core.store.x("""INSERT INTO oauth_tokens(token_hash, kind, client_id, resource, scope, source, family, expires_at)
                  VALUES (?, 'access', ?, ?, 'mcp', 'chatgpt', ?, ?)""",
                 (sha(token), client, core.cfg.resource, family, core.clock() + 3600))
    return token, family


async def test_mcp_activity_routes_key_then_oauth_then_project_then_global(instance):
    open_instance, _ = instance
    core = open_instance(settings={"auth": {"owner_emails": ["owner@example.test"]}})
    core.projects.create(slug="demo", name="Demo")
    route(core, "gemini", project="demo")
    route(core, "grok", agent="owner")
    route(core, "local", agent=None)
    token, family = oauth(core)
    app = create_app(core=core)
    async with core.origins["mcp"].lifespan():
        async with AsyncClient(transport=ASGITransport(app=app), base_url=PUBLIC_URL) as client:
            await acall(client, STATIC_KEY, "switchboard_status")
            project = await core.send(principal(), to="owner", project="demo", task="Key delivery")
            assert project["to"] == "agent:capy"

            core.clock.t += 1
            await acall(client, token, "inbox", {"since_cursor": 0})
            oauth_note = await core.agent_send(principal(), to="agent:owner", text="OAuth delivery", project="demo")
            assert oauth_note["to"] == "chatgpt"
            assert items(core, "chatgpt")[0]["resolved_by"] == "active"

            core.store.x("UPDATE oauth_tokens SET revoked_at = ? WHERE family = ?", (core.clock(), family))
            assert core.owner_delivery.resolve("demo") == ("capy", "active")
            core.clock.t += OWNER_ACTIVE_WINDOW_S
            assert core.owner_delivery.resolve("demo") == ("gemini", "project")
            assert core.owner_delivery.resolve(None) == ("grok", "global")
            assert not items(core, "owner")
            assert [(row["target"], row["detail"], row["excerpt"]) for row in core.store.q(
                "SELECT target, detail, excerpt FROM audit WHERE action = 'owner_delivery' ORDER BY id"
            )] == [("capy", "active", ""), ("chatgpt", "active", "")]


async def test_companion_connected_sign_in_and_scoped_agents_do_not_displace_project_route(instance):
    open_instance, _ = instance
    core = open_instance(settings={"auth": {"key_scopes": {"capy": ["project:other"]}}})
    core.projects.create(slug="demo", name="Demo")
    route(core, "claude", project="demo")
    key = core.auth.verify_bearer(STATIC_KEY)
    for kind in ("owner_session", "connect", "companion", "inbox_key"):
        core.owner_delivery.record_activity(replace(key, kind=kind))
    core.owner_delivery.record_activity(replace(key, trust="agent"))
    assert core.store.one("SELECT COUNT(*) AS n FROM owner_activity")["n"] == 0
    core.owner_delivery.record_activity(key)
    assert core.owner_delivery.resolve("demo") == ("claude", "project")
    assert core.owner_delivery.resolve("other") == ("capy", "active")


async def test_dm_watch_uses_project_default_and_preserves_external_framing(instance, monkeypatch):
    open_instance, _ = instance
    core = open_instance(settings={"blocks": {"telegram": {"spaces": {"home": {"default_target": "agent:claude"}}}}})
    core.projects.create(slug="demo", name="Demo")
    route(core, "grok", agent="owner")
    dm = AsyncMock()
    monkeypatch.setattr(core.blocks["telegram"], "dm_owner", dm)
    watch(core, 0, 50, dm=True, project="demo", block="telegram", space="home")
    [digest] = items(core, "claude")
    handle = core.inbox.handle(digest["handle"])
    assert (handle["owner"], handle["target"], handle["status"]) == ("claude", "agent:claude", "delivered")
    assert handle["meta"]["for_owner"] is True and handle["meta"]["resolved_by"] == "project"
    assert digest["trust"] == "external" and OPEN in digest["text"] and CLOSE in digest["text"]
    assert not items(core, "grok")
    dm.assert_not_awaited()


async def test_pending_survives_restart_and_publishes_after_cursor_advance(instance):
    open_instance, close = instance
    core = open_instance()
    pending = await core.agent_send(principal(), to="owner", text="Private pending message")
    handle = pending["handle"]
    assert not pending["delivered"] and pending["status"] == "pending_owner_route"
    assert core.inbox.handles() == [] and items(core) == []
    assert core.health()["owner_delivery"] == {"pending": 1}
    assert "owner_delivery_pending" in core.health()["problems"]
    assert "owner_delivery" not in core.health(public=True)
    assert "owner_delivery" not in core.health(replace(principal(), kind="connect"))
    await core.agent_send(principal(), to="grok", text="Other update")
    items(core, "grok", advance=True, since=None)
    await close(core)

    core = open_instance()
    assert core.store.owner_archived_count == 0
    assert core.inbox.pending_owner_count() == 1
    route(core, "grok", agent="owner")
    core.modes.flush()
    core.modes.flush()
    [item] = items(core, "grok", since=None)
    assert (item["handle"], item["text"], item["for_owner"], item["resolved_by"]) == (
        handle, "Private pending message", True, "global",
    )
    assert core.health()["owner_delivery"] == {"pending": 0}
    assert "owner_delivery_pending" not in core.health()["problems"]
    assert [(row["outcome"], row["target"], row["detail"]) for row in core.store.q(
        "SELECT outcome, target, detail FROM audit WHERE action = 'owner_delivery' AND ref = ? ORDER BY id", (handle,)
    )] == [("pending", None, "owner_delivery_pending"), ("ok", "grok", "global")]


async def test_cancelled_pending_owner_message_is_not_published_or_listed(instance):
    open_instance, _ = instance
    core = open_instance()
    sent = await core.agent_send(principal(), to="owner", text="Cancel before delivery")
    assert await core.verb(principal(), "cancel", sent["handle"]) == {"conversation": sent["handle"], "status": "cancelled"}
    route(core, "grok", agent="owner")
    core.modes.flush()
    assert not core.inbox.pending_owner_count()
    assert not items(core, "grok") and not core.inbox.handles()


async def test_owner_watch_appends_until_resolved_agent_reads_then_resolves_again(instance):
    open_instance, _ = instance
    core = open_instance()
    watch(core, 0, 100)
    assert core.inbox.pending_owner_count() == 1
    assert not items(core)
    route(core, "claude", agent="owner")
    core.modes.flush()
    [first] = items(core, "claude")
    assert first["count"] == 100
    assert core.inbox.mark_owner_read(first["handle"], first["cursor"], first["count"])

    core.owner_delivery.record_activity(core.auth.verify_bearer(STATIC_KEY))
    watch(core, 100, 150)
    [merged] = items(core, "claude")
    assert (merged["handle"], merged["cursor"], merged["count"], merged["resolved_by"]) == (
        first["handle"], first["cursor"], 250, "global",
    )
    assert len(merged["items"]) == 200
    assert not items(core, "capy")
    assert not core.inbox.read(owner="capy", owner_digests=True)["items"]
    assert not core.inbox.mark_owner_read(merged["handle"], merged["cursor"], first["count"])

    items(core, "claude", advance=True, since=None)
    watch(core, 250, 50)
    [second] = items(core, "capy")
    assert second["handle"] != first["handle"] and second["count"] == 50 and second["resolved_by"] == "active"
    assert len(core.store.q("SELECT 1 FROM audit WHERE action = 'owner_delivery' AND outcome = 'ok'")) == 2


async def test_legacy_owner_migration_archives_21_digests_with_785_events_without_replay(instance):
    open_instance, close = instance
    core = open_instance()
    counts = [37] * 20 + [45]
    for idx, count in enumerate(counts):
        handle = core.inbox.open_handle(owner="owner", destination="mailbox", ref="", project=None, title=REPO,
                                        meta={"owner_notify": {"reason": "no_configured_surface"}}, status="no_owner_surface")
        digest = {"count": count, "block": "github", "space": REPO, "channel": f"repo-{idx}", "dm": False, "items": []}
        core.store.x("INSERT INTO inbox(owner, handle, destination, kind, data, at) VALUES ('owner', ?, 'mailbox', 'watch_digest', ?, ?)",
                     (handle, json.dumps(digest), core.clock()))
    await close(core)

    core = open_instance()
    assert core.store.owner_archived_count == 21
    assert core.store.one("SELECT COUNT(*) AS n FROM handles WHERE json_extract(meta, '$.owner_archived') = 1")["n"] == 21
    assert core.store.one("SELECT SUM(json_extract(data, '$.count')) AS n FROM inbox")["n"] == 785
    row = core.store.one("SELECT handle, id FROM inbox ORDER BY id")
    assert core.inbox.handle(row["handle"]) is None
    assert core.inbox.item(row["id"]) is None
    assert core.inbox.message_handle(core.inbox.message_id(row["id"])) is None
    assert not core.inbox.read(owner=None, handle=row["handle"])["items"]
    with pytest.raises(CoreError, match="no conversation"):
        await core.verb(principal(), "result", row["handle"])
    route(core, "claude", agent="owner")
    core.modes.flush()
    assert not items(core) and not core.inbox.handles()
    assert not core.inbox.pending_owner_count()
    assert not core.store.q("SELECT 1 FROM audit WHERE action = 'owner_delivery'")
    await close(core)
    assert open_instance().store.owner_archived_count == 0
