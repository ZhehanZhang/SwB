import asyncio

import pytest

from switchboard.inbox import Inbox
from switchboard.store import Store
from conftest import Clock


@pytest.fixture
def inbox(tmp_path):
    ib = Inbox(Store(tmp_path / "i.db", clock=Clock()))
    ib.open_handle(handle="h_a", owner="claude", destination="capy", ref="jam_A", project=None, title="a", meta={})
    ib.open_handle(handle="h_b", owner="claude", destination="capy", ref="jam_B", project=None, title="b", meta={})
    ib.open_handle(handle="h_c", owner="chatgpt", destination="capy", ref="jam_C", project=None, title="c", meta={})
    return ib


async def test_cursor_per_reader_and_handle(inbox):
    await inbox.add("h_a", "reply", {"text": "one"})
    await inbox.add("h_c", "reply", {"text": "not yours"})
    await inbox.add("h_b", "reply", {"text": "two"})
    first = inbox.read(owner="claude")
    assert [i["text"] for i in first["items"]] == ["one", "two"]
    assert inbox.read(owner="claude")["items"] == []
    assert [i["text"] for i in inbox.read(owner="claude", handle="h_b")["items"]] == ["two"]
    assert [i["text"] for i in inbox.read(owner="claude", since=0)["items"]] == ["one", "two"]
    assert [i["text"] for i in inbox.read(owner="chatgpt")["items"]] == ["not yours"]
    everything = inbox.read(owner=None, reader="channel:x", advance=False)
    assert len(everything["items"]) == 3 and inbox.read(owner=None, reader="channel:x")["cursor"] == everything["cursor"]
    assert inbox.head() == everything["cursor"]
    await inbox.add("h_a", "final", {"text": "done"})
    assert [i["kind"] for i in inbox.read(owner="claude")["items"]] == ["final"]


async def test_wait_returns_early_and_times_out_cleanly(inbox):
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    empty = await inbox.wait(owner="claude", handle="h_a", timeout=0.05)
    assert empty["items"] == [] and empty["timed_out"] is True and loop.time() - t0 >= 0.05

    async def later():
        await asyncio.sleep(0.05)
        await inbox.add("h_c", "reply", {"text": "other owner"})
        await asyncio.sleep(0.05)
        await inbox.add("h_a", "reply", {"text": "hello"})

    task = asyncio.create_task(later())
    t0 = loop.time()
    got = await inbox.wait(owner="claude", handle="h_a", timeout=5)
    assert [i["text"] for i in got["items"]] == ["hello"] and got["timed_out"] is False and loop.time() - t0 < 2
    await task


async def test_filtered_read_preserves_unconsumed_root_and_child_items(inbox):
    inbox.open_handle(handle="h_child", owner="claude", destination="mailbox", ref="beta", project=None, title=None, meta={"root": "h_a"})
    await inbox.add("h_a", "reply", {"text": "root item outside the block filter"})
    await inbox.add("h_child", "message", {"text": "consumed child item"})
    await inbox.add("h_b", "reply", {"text": "unrelated item"})
    await inbox.add("h_child", "message", {"text": "child item beyond the limit"})

    filtered = inbox.read(owner="claude", handle="h_a", destination="mailbox", limit=1)
    assert [i["text"] for i in filtered["items"]] == ["consumed child item"]
    assert [i["text"] for i in inbox.read(owner="claude")["items"]] == [
        "root item outside the block filter", "unrelated item", "child item beyond the limit",
    ]
