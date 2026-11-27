from __future__ import annotations

import re

import httpx

from ...claims import ClaimInputError, Field

TOKEN_PREFIXES = {"slack_bot": "xoxb-", "slack_user": "xoxp-", "slack_app": "xapp-"}


def slack_workspace(label: str, names: dict[str, str]) -> tuple[str, str, list[Field]]:
    apps = "https://api.slack.com/apps"
    text = {"bot": ("Bot token", "OAuth & Permissions, Bot User OAuth Token (xoxb-)."),
            "user": ("User token", "OAuth & Permissions, User OAuth Token (xoxp-)."),
            "app": ("App-level token", "Basic Information, App-Level Tokens, with connections:write (xapp-).")}
    fields = [Field(env, text[kind][0], f"slack_{kind}", text[kind][1], link=apps) for kind, env in names.items()]
    return f"Slack workspace {label}", "slack", fields


def validate_token(kind: str, value: str) -> None:
    prefix = TOKEN_PREFIXES[kind]
    rotating = r"(?:xoxe\.)?" if kind != "slack_app" else ""
    if not re.fullmatch(rotating + prefix + r"[A-Za-z0-9_-]+", value):
        raise ClaimInputError(f"Use a Slack token that starts with {prefix}")


async def check_slack_auth(kind: str, token: str) -> bool | None:
    method = "apps.connections.open" if kind == "slack_app" else "auth.test"
    try:
        async with httpx.AsyncClient(timeout=3) as http:
            response = await http.post(f"https://slack.com/api/{method}", headers={"Authorization": f"Bearer {token}"})
        if response.status_code == 429 or response.status_code >= 500:
            return None
        data = response.json()
    except (httpx.RequestError, ValueError):
        return None
    if isinstance(data, dict) and data.get("error") not in ("internal_error", "fatal_error", "service_unavailable", "ratelimited"):
        if isinstance(ok := data.get("ok"), bool):
            return ok
    return None
