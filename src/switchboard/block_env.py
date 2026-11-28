from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


CAPY_KEY = "CAPY_API_KEY"
CAPY_SEND_KEY = "CAPY_API_KEY_SERVICE"
CAPY_AGENT_KEY_PREFIX = "CAPY_API_KEY_AS_"
GITHUB_SECRET = "GITHUB_WEBHOOK_SECRET"
CHAT_BOT_TOKENS = {"discord": "DISCORD_BOT_TOKEN", "telegram": "TELEGRAM_BOT_TOKEN"}
SLACK_TOKEN = re.compile(r"SLACK(?:_([A-Z0-9][A-Z0-9_]{0,31}))?_(BOT|APP|USER)_TOKEN")
CLAUDE_TOKEN = re.compile(r"CLAUDE_ROUTINE_TOKEN(?:_([A-Z0-9][A-Z0-9_]{0,39}))?")
GROK_TOKEN = re.compile(r"GROK_ROUTINE(?:_([A-Z0-9][A-Z0-9_]{0,39}))?_(?:SECRET|URL)")


def claude_token_env(name: str) -> str:
    return f"CLAUDE_ROUTINE_TOKEN_{name.upper().replace('-', '_')}"


def grok_credential_envs(block: str, name: str) -> tuple[str, str]:
    prefix = f"{block}_{name}".upper().replace("-", "_")
    return f"{prefix}_URL", f"{prefix}_SECRET"


def default_blocks() -> dict[str, dict[str, Any]]:
    return {"mcp": {"use": "mcp"}, "webhook": {"use": "webhook"}}


def env_blocks(env: Mapping[str, str]) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[str, ...]]]:
    blocks = default_blocks()
    required: dict[str, tuple[str, ...]] = {
        "mcp": (), "webhook": (), "slack": ("SLACK_BOT_TOKEN", "SLACK_TEAM_ID"), "capy": (CAPY_KEY, CAPY_SEND_KEY),
        "github": (GITHUB_SECRET,), "claude-routine": ("CLAUDE_ROUTINE_TOKEN", "CLAUDE_ROUTINE_ID"),
        "grok-routine": ("GROK_ROUTINE_SECRET", "GROK_ROUTINE_URL"), "dots": (),
        **{block: (token,) for block, token in CHAT_BOT_TOKENS.items()},
    }
    present = sorted(name for name, value in env.items() if value)
    if any(name in required["capy"] or name.startswith(CAPY_AGENT_KEY_PREFIX) and len(name) > len(CAPY_AGENT_KEY_PREFIX) for name in present):
        blocks["capy"] = {"use": "capy"}
    if env.get(GITHUB_SECRET):
        blocks["github"] = {"use": "github"}
    for block, token in CHAT_BOT_TOKENS.items():
        if env.get(token):
            blocks[block] = {"use": block}

    slack: dict[str, set[str]] = {}
    claude, grok = set(), set()
    for name in present:
        if match := SLACK_TOKEN.fullmatch(name):
            slack.setdefault(match[1] or "", set()).add(match[2])
        elif match := CLAUDE_TOKEN.fullmatch(name):
            claude.add(match[1] or "")
        elif match := GROK_TOKEN.fullmatch(name):
            grok.add(match[1] or "")

    if slack:
        workspaces, grants, needs = {}, {}, []
        for suffix, tokens in slack.items():
            label = suffix.lower() or "default"
            prefix = f"SLACK_{suffix}" if suffix else "SLACK"
            team, app = f"{prefix}_TEAM_ID", f"{prefix}_APP_TOKEN"
            identities = {}
            for identity in ("BOT", "USER"):
                if identity in tokens or identity == "BOT" and "USER" not in tokens:
                    token = f"{prefix}_{identity}_TOKEN"
                    identities[identity.lower()] = {"token_env": token}
                    needs.append(token)
            needs.append(team)
            if "APP" in tokens:
                needs.append(app)
            if env.get(team):
                workspaces[label] = {"team_id": env[team], "identities": identities,
                                     **({"app_token_env": app} if "APP" in tokens else {})}
                grants[label] = list(identities)
        blocks["slack"] = {"use": "slack", "workspaces": workspaces, "agents": {"owner": {"workspaces": grants}}}
        required["slack"] = tuple(needs)

    if claude:
        routines, needs = {}, []
        for suffix in sorted(claude):
            suffix = f"_{suffix}" if suffix else ""
            token, rid = f"CLAUDE_ROUTINE_TOKEN{suffix}", f"CLAUDE_ROUTINE_ID{suffix}"
            name = suffix[1:].lower().replace("_", "-") or "claude"
            routines[name] = {"token_env": token, "routine_id": env.get(rid)}
            needs.extend((token, rid))
        blocks["claude-routine"] = {"use": "claude-routine", "routines": routines}
        required["claude-routine"] = tuple(needs)

    if grok:
        routines, needs = {}, []
        for suffix in sorted(grok):
            name = suffix.lower().replace("_", "-") or "grok"
            prefix = f"GROK_ROUTINE_{suffix}" if suffix else "GROK_ROUTINE"
            secret, url = f"{prefix}_SECRET", f"{prefix}_URL"
            routines[name] = {"secret_env": secret, "url_env": url}
            needs.extend((secret, url))
        blocks["grok-routine"] = {"use": "grok-routine", "routines": routines}
        required["grok-routine"] = tuple(needs)

    return blocks, required


def configured_env(name: str, config: dict[str, Any]) -> tuple[str, ...]:
    def items(raw: Any) -> list[tuple[str, dict[str, Any]]]:
        return [(str(k), v or {}) for k, v in raw.items() if v is None or isinstance(v, dict)] if isinstance(raw, dict) else []

    kind = config.get("use") or name
    if kind == "capy":
        send_key = config.get("send_key_env") or CAPY_SEND_KEY
        return (CAPY_KEY, send_key) if config.get("identity", "service") == "service" and send_key != CAPY_KEY else (CAPY_KEY,)
    if kind == "github":
        return (config.get("secret_env", GITHUB_SECRET), *(sink[key] for _, sink in items(config.get("sinks"))
                for key in ("url_env", "secret_env") if sink.get(key) and (key == "url_env" or sink.get("signature") == "stripe")))
    if kind in CHAT_BOT_TOKENS:
        return (config.get("token_env", CHAT_BOT_TOKENS[kind]),)
    if kind == "slack" and config.get("engine", True):
        return tuple(token for _, ws in items(config.get("workspaces"))
                     for token in [*(i.get("token_env") for _, i in items(ws.get("identities"))),
                                   ws.get("app_token_env") if ws.get("intake", "socket") == "socket" else None] if token)
    if kind == "claude-routine":
        return tuple(routine.get("token_env") or claude_token_env(label) for label, routine in items(config.get("routines")))
    if kind == "grok-routine":
        return tuple(routine.get(key, env_name) for label, routine in items(config.get("routines"))
                     for key, env_name in zip(("url_env", "secret_env"), grok_credential_envs(name, label)))
    return ()
