from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ALLOWED_SUBTYPES = frozenset({"bot_message", "thread_broadcast", "file_share", "me_message"})


@dataclass(frozen=True)
class ClassifyContext:
    has_bot: bool = False
    bot_user_id: str | None = None
    bot_id: str | None = None
    app_id: str | None = None
    watched: frozenset[str] = field(default_factory=frozenset)
    skip_user_ids: frozenset[str] = field(default_factory=frozenset)


def classify(event: dict[str, Any], ctx: ClassifyContext) -> str | None:
    """Return the push kind for a Slack message/app_mention event, or None to drop it."""
    etype = event.get("type")
    if etype not in ("message", "app_mention"):
        return None
    subtype = event.get("subtype")
    if (subtype and subtype not in ALLOWED_SUBTYPES) or event.get("hidden"):
        return None
    user = event.get("user")
    if ctx.bot_user_id and user == ctx.bot_user_id:
        return None
    if ctx.bot_id and event.get("bot_id") == ctx.bot_id:
        return None
    if ctx.app_id and ctx.app_id in (event.get("app_id"), (event.get("bot_profile") or {}).get("app_id")):
        return None
    if user and user in ctx.skip_user_ids:
        return None
    if etype == "app_mention":
        return "mention"
    if ctx.has_bot and event.get("channel_type") == "im":
        return "dm"
    if ctx.bot_user_id and f"<@{ctx.bot_user_id}>" in (event.get("text") or ""):
        return "mention"
    if event.get("channel") in ctx.watched:
        return "watched"
    return None


def permalink(team_url: str | None, channel: str, ts: str, thread_ts: str | None = None) -> str | None:
    if not team_url:
        return None
    link = f"{team_url.rstrip('/')}/archives/{channel}/p{ts.replace('.', '')}"
    if thread_ts and thread_ts != ts:
        link += f"?thread_ts={thread_ts}&cid={channel}"
    return link


def event_id(team: str, channel: str, ts: str) -> str:
    return f"{team}:{channel}:{ts}"


def build_event(
    *,
    workspace: str,
    team: str,
    event: dict[str, Any],
    kind: str,
    channel_name: str | None,
    user_name: str | None,
    team_url: str | None,
    max_text_chars: int,
    addressed_to_us: bool = False,
) -> dict[str, Any]:
    channel = event["channel"]
    ts = event["ts"]
    thread_ts = event.get("thread_ts")
    text = event.get("text") or ""
    truncated = len(text) > max_text_chars
    if truncated:
        text = text[:max_text_chars]
    return {
        "id": event_id(team, channel, ts),
        "kind": kind,
        "workspace": workspace,
        "team": team,
        "channel": channel,
        "channel_name": channel_name,
        "user": event.get("user") or event.get("bot_id"),
        "user_name": user_name or event.get("username"),
        "is_bot": bool(event.get("bot_id")),
        "addressed_to_us": addressed_to_us,
        "ts": ts,
        "thread_ts": thread_ts,
        "text": text,
        "truncated": truncated,
        "files": len(event.get("files") or []),
        "permalink": permalink(team_url, channel, ts, thread_ts),
    }


def render_payload(events: list[dict[str, Any]], batched: bool) -> dict[str, Any]:
    """Body POSTed to a webhook sink: one event as-is, or a batch envelope when coalescing."""
    if not batched:
        (only,) = events
        return {"type": "slackgate.event", **only}
    return {
        "type": "slackgate.batch",
        "workspace": events[0]["workspace"] if events else None,
        "count": len(events),
        "events": events,
    }
