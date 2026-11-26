import asyncio

import httpx

from switchboard.slack.config import SinkConfig
from switchboard.slack.events import build_event
from switchboard.slack.push import WebhookSink
from slackfix import clock, hooks, make_core, world  # noqa: F401  (fixtures)


def sink_cfg(**kw):
    base = dict(name="s", url="https://hooks.example/x", headers={"Authorization": "Bearer t"}, kinds=("watched", "mention", "dm"), channels=(), coalesce_seconds=0, max_batch=20)
    base.update(kw)
    return SinkConfig(**base)


def event(ts="1700000000.000100", kind="watched", channel="C1", name="general"):
    return build_event(
        workspace="a2a",
        team="T1",
        event={"type": "message", "channel": channel, "ts": ts, "user": "U1", "text": "x" * 50, "thread_ts": "1699999999.000001"},
        kind=kind,
        channel_name=name,
        user_name="alice",
        team_url="https://workspace-a.example.test/",
        max_text_chars=10,
    )


def test_event_format():
    ev = event()
    assert ev == {
        "id": "T1:C1:1700000000.000100",
        "kind": "watched",
        "workspace": "a2a",
        "team": "T1",
        "channel": "C1",
        "channel_name": "general",
        "user": "U1",
        "user_name": "alice",
        "is_bot": False,
        "addressed_to_us": False,
        "ts": "1700000000.000100",
        "thread_ts": "1699999999.000001",
        "text": "x" * 10,
        "truncated": True,
        "files": 0,
        "permalink": "https://workspace-a.example.test/archives/C1/p1700000000000100?thread_ts=1699999999.000001&cid=C1",
    }


async def test_webhook_posts_single_event_with_headers():
    seen = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(204)))
    sink = WebhookSink("a2a", sink_cfg(), http)
    assert await sink.deliver({"type": "slackgate.event", **event()})
    assert seen[0].headers["authorization"] == "Bearer t"
    assert seen[0].url == "https://hooks.example/x"


async def test_webhook_retries_then_succeeds(monkeypatch):
    statuses = [500, 429, 200]
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(statuses.pop(0), headers={"retry-after": "0"})))
    sink = WebhookSink("a2a", sink_cfg(), http)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    assert await sink.deliver({"x": 1})
    assert sink.delivered == 1 and statuses == []


async def test_webhook_gives_up_on_4xx():
    calls = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(404)))
    sink = WebhookSink("a2a", sink_cfg(), http)
    assert not await sink.deliver({"x": 1})
    assert len(calls) == 1 and sink.failed == 1


async def test_coalescing_batches_events():
    bodies = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: bodies.append(r.content) or httpx.Response(200)))
    sink = WebhookSink("a2a", sink_cfg(coalesce_seconds=0.2, max_batch=3), http)
    sink.start()
    for i in range(4):
        sink.put(event(ts=f"1700000000.00000{i}"))
    await asyncio.sleep(0.5)
    await sink.stop()
    import json

    parsed = [json.loads(b) for b in bodies]
    assert [p["count"] for p in parsed] == [3, 1]
    assert all(p["type"] == "slackgate.batch" for p in parsed)


def test_sink_filters():
    sink = WebhookSink("a2a", sink_cfg(kinds=("mention",), channels=("#general",)), httpx.AsyncClient())
    assert sink.accepts(event(kind="mention"))
    assert not sink.accepts(event(kind="watched"))
    assert not sink.accepts(event(kind="mention", name="random"))
