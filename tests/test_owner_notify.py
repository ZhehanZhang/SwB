import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from conftest import Clock, principal

from switchboard.config import ConfigError
from switchboard.core import Switchboard

PUBLIC_URL = "https://office.example.com"
REPO = "example-org/app"


@pytest.fixture
async def instance(tmp_path):
    instances = []
    clock = Clock()

    def open_instance(*, name="data", settings=None):
        path = tmp_path / f"{name}.yaml"
        if settings is not None or not path.exists():
            path.write_text(yaml.safe_dump({
                "version": 2,
                "public_url": PUBLIC_URL,
                "modes": {"digest_minutes": 1},
                "blocks": {
                    "telegram": {"spaces": {"home": {"owner_ids": ["101"]}, "spare": {"owner_ids": ["102"]}}},
                    "discord": {"spaces": {"home": {"guild_id": "100000000000000001", "owner_ids": ["100000000000000002"]}}},
                },
                **(settings or {}),
            }), encoding="utf-8")
        core = Switchboard(path, tmp_path / name, env={}, clock=clock, slack_options={"connect": False})
        instances.append(core)
        return core

    async def close(core):
        await core.aclose()
        instances.remove(core)

    yield open_instance, close
    for core in instances:
        await core.aclose()


def note(core):
    return core.inbox.deliver_triage_note(source="claude", project=None, data={"text": "Synthetic private note"})


def watch(core, start, count, *, target=None, dm=False, project=None):
    for i in range(start, start + count):
        core.modes.watch("github", REPO, REPO, str(i), "321" if i % 2 else "", None, f"Test excerpt {i}",
                         None, target, dm=dm, project=project)


async def test_owner_delivery_has_sanitized_title_count_deep_link_and_audit(instance, monkeypatch):
    open_instance, _ = instance
    core = open_instance(settings={"auth": {"source_names": {"example": "owner"}}})
    telegram, discord = core.blocks["telegram"], core.blocks["discord"]
    open_dm, post, other = AsyncMock(return_value="101"), AsyncMock(return_value="1"), AsyncMock()
    monkeypatch.setattr(telegram, "open_dm", open_dm)
    monkeypatch.setattr(telegram, "post", post)
    monkeypatch.setattr(discord, "dm_owner", other)
    explicit_title = "Synthetic <!channel> [link](https://ignored.example) " + "a" * 160
    result = await core.agent_send(principal(), to="owner", title=explicit_title, text="Private body contents")
    handle = result["handle"]
    assert core.inbox.handle(handle)["status"] == "stored"
    assert await core.owner_notify.flush()
    open_dm.assert_awaited_once_with("home", "101")
    space, channel, text = post.await_args.args
    title, count, link = text.splitlines()
    assert (space, channel, count, link) == ("home", "101", "1 new update", f"{PUBLIC_URL}/inbox/{handle}")
    assert title.startswith("Synthetic link") and len(title) <= 100
    assert all(value not in text for value in ("Private body contents", "ignored.example", "<!channel>"))
    other.assert_not_awaited()
    row = core.inbox.handle(handle)
    detail = row["meta"]["owner_notify"]
    assert row["status"] == "notified" and detail["surface"] == "telegram:home" and detail["notified_at"].endswith("+00:00")
    [entry] = core.store.q("SELECT outcome, ref, target, excerpt FROM audit WHERE action = 'owner_notify'")
    assert tuple(entry) == ("notified", handle, "telegram:home", "")


async def test_explicit_order_failure_and_redelivery_share_the_same_window(instance, monkeypatch):
    open_instance, _ = instance
    core = open_instance(settings={"owner_notify": ["absent:home", "discord:home", "telegram:home"]})
    discord, telegram = AsyncMock(side_effect=[False, True]), AsyncMock()
    monkeypatch.setattr(core.blocks["discord"], "dm_owner", discord)
    monkeypatch.setattr(core.blocks["telegram"], "dm_owner", telegram)
    handle = note(core)
    assert await core.owner_notify.flush()
    row = core.inbox.handle(handle)
    assert row["status"] == "notify_failed"
    assert row["meta"]["owner_notify"] == {"surface": "discord:home", "reason": "send_failed"}
    assert core.inbox.redeliver_owner(handle)
    assert core.inbox.handle(handle)["status"] == "stored"
    assert not core.inbox.redeliver_owner(handle)
    assert not await core.owner_notify.flush()
    core.clock.t += 60
    assert await core.owner_notify.flush()
    assert core.inbox.handle(handle)["status"] == "notified"
    assert discord.await_count == 2 and discord.await_args.kwargs == {"space": "home"}
    telegram.assert_not_awaited()
    assert [row["outcome"] for row in core.store.q(
        "SELECT outcome FROM audit WHERE action = 'owner_notify' ORDER BY id"
    )] == ["notify_failed", "notified"]


async def test_throttle_is_per_selected_destination(instance, monkeypatch):
    open_instance, _ = instance
    core = open_instance()
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(core.blocks["telegram"], "dm_owner", send)
    note(core)
    assert await core.owner_notify.flush()
    note(core)
    core.cfg.owner_notify = ("telegram:spare",)
    assert await core.owner_notify.flush()
    handle = note(core)
    core.cfg.owner_notify = ("telegram:home",)
    assert not await core.owner_notify.flush()
    assert core.inbox.handle(handle)["status"] == "stored"
    assert [entry.kwargs["space"] for entry in send.await_args_list] == ["home", "spare"]


@pytest.mark.parametrize("owner_setting", [{"owner_user_ids": ["U123OWNER"]}, {"approver_user_id": "U123OWNER"}])
async def test_slack_posts_only_to_the_selected_workspace_owner(instance, owner_setting):
    open_instance, _ = instance
    core = open_instance(settings={
        "owner_notify": ["slack:team"],
        "blocks": {"slack": {"workspaces": {
            "team": {"team_id": "TTEAM0001", "identities": {"bot": {"token_env": "TEAM_BOT"}}, **owner_setting},
            "other": {"team_id": "TOTHER001", "identities": {"bot": {"token_env": "OTHER_BOT"}}, "approver_user_id": "U456OTHER"},
        }}},
    })
    bot = SimpleNamespace(conversations_open=AsyncMock(return_value={"channel": {"id": "DTEST1234"}}),
                          chat_postMessage=AsyncMock())
    other = SimpleNamespace(conversations_open=AsyncMock())
    engine = core.blocks["slack"].engine
    engine.runtimes.update(team=SimpleNamespace(clients={"bot": bot}, stop=AsyncMock()),
                           other=SimpleNamespace(clients={"bot": other}, stop=AsyncMock()))
    handle = note(core)
    assert await core.owner_notify.flush()
    bot.conversations_open.assert_awaited_once_with(users="U123OWNER")
    bot.chat_postMessage.assert_awaited_once_with(
        channel="DTEST1234", text=f"Triage note\n1 new update\n{PUBLIC_URL}/inbox/{handle}",
        mrkdwn=False, link_names=False, unfurl_links=False, unfurl_media=False,
    )
    other.conversations_open.assert_not_awaited()
    assert core.inbox.handle(handle)["meta"]["owner_notify"]["surface"] == "slack:team"



@pytest.mark.parametrize(("settings", "reason"), [
    ({"owner_notify": []}, "disabled"),
    ({"owner_notify": ["telegram:missing"]}, "no_configured_surface"),
    ({"blocks": {}}, "no_configured_surface"),
])
async def test_absent_surface_keeps_mail_and_reports_why(instance, settings, reason):
    open_instance, _ = instance
    core = open_instance(settings=settings)
    handle = note(core)
    assert await core.owner_notify.flush()
    row = core.inbox.handle(handle)
    assert row["status"] == "no_owner_surface" and row["meta"]["owner_notify"] == {"reason": reason}
    assert core.inbox.read(owner="owner", since=0, advance=False)["items"][0]["handle"] == handle


@pytest.mark.parametrize("value", ["telegram:home", ["telegram"], ["telegram:home:123"], [False], None])
def test_owner_notify_requires_a_list_of_surface_addresses(instance, value):
    open_instance, _ = instance
    with pytest.raises(ConfigError, match="owner_notify"):
        open_instance(settings={"owner_notify": value})


async def test_watch_and_notes_share_a_persistent_window_and_startup_drains_queue(instance, monkeypatch):
    open_instance, close = instance
    core = open_instance()
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(core.blocks["telegram"], "dm_owner", send)
    watch(core, 0, 50)
    assert await core.owner_notify.flush()
    watch(core, 50, 50)
    handle = note(core)
    assert not await core.owner_notify.flush() and send.await_count == 1
    core.clock.t += 59
    await close(core)
    core = open_instance()
    monkeypatch.setattr(core.blocks["telegram"], "dm_owner", send)
    assert core.inbox.handle(handle)["status"] == "stored"
    assert not await core.owner_notify.flush()
    core.clock.t += 1
    posted = asyncio.Event()

    async def dm(text, *, space):
        await send(text, space=space)
        posted.set()
        return True

    monkeypatch.setattr(core.blocks["telegram"], "dm_owner", dm)
    await core.start()
    await asyncio.wait_for(posted.wait(), 2)
    await asyncio.wait_for(_notified(core, handle), 2)
    assert send.await_count == 2
    text = send.await_args.args[0]
    assert text == f"Watch digest for {REPO}\n51 new updates\n{PUBLIC_URL}/inbox"
    assert (await core.owner_notify.flush()) is False


async def test_work_queued_during_a_dm_survives_that_send(instance, monkeypatch):
    open_instance, _ = instance
    core = open_instance()
    watch(core, 0, 50)
    async def during(_text, *, space):
        watch(core, 50, 50)
        return True

    monkeypatch.setattr(core.blocks["telegram"], "dm_owner", during)
    assert await core.owner_notify.flush()
    [item] = core.inbox.read(owner="owner", advance=False)["items"]
    assert item["count"] == 100 and core.inbox.handle(item["handle"])["status"] == "stored"
    assert not await core.owner_notify.flush()
    core.clock.t += 60
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(core.blocks["telegram"], "dm_owner", send)
    assert await core.owner_notify.flush()
    assert "50 new updates" in send.await_args.args[0]
    assert core.inbox.handle(item["handle"])["status"] == "notified"


async def _notified(core, handle):
    while core.inbox.handle(handle)["status"] == "stored":
        await asyncio.sleep(0.001)


async def test_watch_appends_until_exact_version_is_read_keeps_count_and_caps_items(instance):
    open_instance, _ = instance
    core = open_instance(settings={"owner_notify": []})
    watch(core, 0, 50)
    [initial] = core.inbox.read(owner="owner", advance=False)["items"]
    assert core.inbox.handle(initial["handle"])["meta"]["from"] == {"kind": "watch", "block": "github", "space": REPO}
    assert [item["author"] for item in initial["items"][:2]] == ["unknown", "321"]
    before = core.inbox.handle(initial["handle"])["updated_at"]
    core.clock.t += 1
    watch(core, 50, 200)
    watch(core, 249, 1)
    [merged] = core.inbox.read(owner="owner", advance=False)["items"]
    assert (merged["handle"], merged["cursor"], merged["count"], len(merged["items"])) == (initial["handle"], initial["cursor"], 250, 200)
    assert "Test excerpt 50\n" in merged["items"][0]["excerpt"]
    assert "Test excerpt 249\n" in merged["items"][-1]["excerpt"]
    assert core.inbox.handle(initial["handle"])["updated_at"] > before
    assert not core.inbox.mark_owner_read(initial["handle"], initial["cursor"], initial["count"])
    assert core.inbox.mark_owner_read(merged["handle"], merged["cursor"], merged["count"])
    watch(core, 250, 50)
    [next_digest] = core.inbox.read(owner="owner", advance=False)["items"]
    assert next_digest["handle"] != merged["handle"] and next_digest["count"] == 50


async def test_watch_partitions_dm_target_and_project_and_respects_target_mcp_read(instance):
    open_instance, _ = instance
    core = open_instance(settings={"owner_notify": []})
    watch(core, 0, 50, target="claude", project="example")
    watch(core, 50, 50, target="claude", project="other")
    watch(core, 100, 50)
    watch(core, 150, 50, dm=True)
    rows = core.inbox.read(owner=None, since=0, advance=False)["items"]
    assert [(item["owner"], item["dm"], core.inbox.handle(item["handle"])["project"]) for item in rows] == [
        ("claude", False, "example"), ("claude", False, "other"), ("owner", False, None), ("owner", True, None),
    ]
    assert core.store.one("SELECT SUM(count) AS events FROM owner_notify_queue")["events"] == 100
    assert core.inbox.read(owner="claude", project="example")["items"][0]["handle"] == rows[0]["handle"]
    watch(core, 200, 50, target="claude", project="example")
    [new] = core.inbox.read(owner="claude", project="example", advance=False)["items"]
    assert new["count"] == 50 and new["handle"] != rows[0]["handle"]


async def test_new_github_events_can_join_the_latest_legacy_digest_without_backfill(instance, monkeypatch):
    open_instance, _ = instance
    core = open_instance()
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(core.blocks["telegram"], "dm_owner", send)
    handle = core.inbox.open_handle(owner="owner", destination="mailbox", ref="", project=None, title="Legacy digest",
                                    meta={}, status="delivered")
    core.store.x("""INSERT INTO inbox(owner, handle, destination, kind, data, at)
        VALUES ('owner', ?, 'mailbox', 'watch_digest', ?, ?)""", (handle, json.dumps({
            "block": "github", "space": REPO, "channel": REPO, "count": 1,
            "items": [{"author": "", "excerpt": "Legacy event", "link": None, "at": core.clock()}],
        }), core.clock()))
    assert not await core.owner_notify.flush()
    watch(core, 0, 50)
    [item] = core.inbox.read(owner="owner", advance=False)["items"]
    assert item["handle"] == handle and item["count"] == 51 and item["items"][0]["author"] == "unknown"
    assert await core.owner_notify.flush()
    assert send.await_args.args[0] == f"Legacy digest\n50 new updates\n{PUBLIC_URL}/inbox/{handle}"
