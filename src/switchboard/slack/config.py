from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

import yaml

IDENTITIES = ("bot", "user")
POLICIES = ("free", "approval", "never")
KINDS = ("watched", "mention", "dm")
INTAKES = ("socket", "poll", "none")
LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
TEAM_RE = re.compile(r"^[TE][A-Z0-9]{2,}$")
AVATAR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\.(png|jpe?g|gif)$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    icon_url: str | None = None
    emoji: str | None = None


@dataclass(frozen=True)
class SinkConfig:
    name: str
    url: str | None
    headers: Mapping[str, str]
    kinds: tuple[str, ...]
    channels: tuple[str, ...]
    coalesce_seconds: float
    max_batch: int


@dataclass(frozen=True)
class IdentityConfig:
    name: str
    token_env: str
    write: str


@dataclass(frozen=True)
class WorkspaceConfig:
    label: str
    team_id: str
    user_only: bool
    app_token_env: str | None
    identities: Mapping[str, IdentityConfig]
    watched: tuple[str, ...]
    sinks: tuple[SinkConfig, ...]
    intake: str
    poll_seconds: float
    default_persona: str | None
    ack_reaction: str | None
    skip_user_ids: frozenset[str]
    app_id: str | None = None
    bot_thread_posts_per_hour: int = 20

    @property
    def has_bot(self) -> bool:
        return "bot" in self.identities


@dataclass(frozen=True)
class Grant:
    """What one switchboard agent (an OAuth agent name or a bearer key name) may do in Slack."""

    id: str
    persona: str | None
    allow_persona_override: bool
    allowed_personas: tuple[str, ...] | None
    scopes: Mapping[str, tuple[str, ...]]

    def identities_for(self, workspace: str) -> tuple[str, ...]:
        return self.scopes.get(workspace, ())


@dataclass(frozen=True)
class ApprovalConfig:
    ttl_minutes: int = 60
    notify_workspace: str | None = None
    approver_user_id: str | None = None


@dataclass(frozen=True)
class Config:
    workspaces: Mapping[str, WorkspaceConfig] = field(default_factory=dict)
    personas: Mapping[str, Persona] = field(default_factory=dict)
    grants: Mapping[str, Grant] = field(default_factory=dict)
    approvals: ApprovalConfig = ApprovalConfig()
    public_base_url: str | None = None
    max_text_chars: int = 4000
    event_retention_days: int = 14

    def grant(self, agent: str) -> Grant | None:
        return self.grants.get(agent)


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise ConfigError(msg)


def _str_list(value: Any, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    _need(isinstance(value, list) and all(isinstance(v, str) for v in value), f"{where} must be a list of strings")
    return tuple(value)


def _parse_persona(pid: str, raw: Any, public_base_url: str | None) -> Persona:
    _need(isinstance(raw, dict), f"personas.{pid} must be a mapping")
    _need(bool(LABEL_RE.match(pid)), f"persona id {pid!r} must match {LABEL_RE.pattern}")
    name = raw.get("name")
    _need(isinstance(name, str) and 0 < len(name) <= 80, f"personas.{pid}.name is required (max 80 chars)")
    icon_url = raw.get("icon_url")
    avatar = raw.get("avatar")
    emoji = raw.get("emoji")
    _need(not (icon_url and avatar), f"personas.{pid}: set icon_url or avatar, not both")
    if icon_url is not None:
        _need(isinstance(icon_url, str) and icon_url.startswith("https://"), f"personas.{pid}.icon_url must be an https URL")
    if avatar is not None:
        _need(isinstance(avatar, str) and bool(AVATAR_RE.match(avatar)), f"personas.{pid}.avatar must be a file name like {pid}.png")
        _need(bool(public_base_url), f"personas.{pid}.avatar needs public_base_url so Slack can fetch it")
        icon_url = f"{public_base_url}/avatars/{avatar}"
    if emoji is not None:
        _need(isinstance(emoji, str) and re.fullmatch(r":[a-z0-9_+-]+:", emoji) is not None, f"personas.{pid}.emoji must look like :robot_face:")
    return Persona(id=pid, name=name, icon_url=icon_url, emoji=emoji)


def _parse_sink(label: str, idx: int, raw: Any, env: Mapping[str, str]) -> SinkConfig:
    where = f"workspaces.{label}.sinks[{idx}]"
    _need(isinstance(raw, dict), f"{where} must be a mapping")
    _need(raw.get("type", "webhook") == "webhook", f"{where}.type: only 'webhook' is supported")
    _need(not (raw.get("url") and raw.get("url_env")), f"{where}: set url or url_env, not both")
    url = raw.get("url") or (env.get(raw["url_env"]) if raw.get("url_env") else None) or None
    if raw.get("url"):
        _need(str(url).startswith(("https://", "http://")), f"{where}.url must be http(s)")
    headers: dict[str, str] = {}
    for header, env_name in (raw.get("headers_env") or {}).items():
        value = env.get(env_name)
        if value:
            headers[str(header)] = value
    kinds = _str_list(raw.get("kinds"), f"{where}.kinds") or KINDS
    _need(all(k in KINDS for k in kinds), f"{where}.kinds must be a subset of {list(KINDS)}")
    coalesce = float(raw.get("coalesce_seconds", 0))
    _need(0 <= coalesce <= 600, f"{where}.coalesce_seconds must be 0..600")
    max_batch = int(raw.get("max_batch", 20))
    _need(1 <= max_batch <= 200, f"{where}.max_batch must be 1..200")
    return SinkConfig(
        name=str(raw.get("name") or f"{label}-sink{idx}"),
        url=url,
        headers=headers,
        kinds=kinds,
        channels=_str_list(raw.get("channels"), f"{where}.channels"),
        coalesce_seconds=coalesce,
        max_batch=max_batch,
    )


def _parse_workspace(label: str, raw: Any, env: Mapping[str, str], personas: Mapping[str, Persona]) -> WorkspaceConfig:
    where = f"workspaces.{label}"
    _need(bool(LABEL_RE.match(label)), f"workspace label {label!r} must match {LABEL_RE.pattern}")
    _need(isinstance(raw, dict), f"{where} must be a mapping")
    team_id = raw.get("team_id")
    _need(isinstance(team_id, str) and bool(TEAM_RE.match(team_id)), f"{where}.team_id must be a Slack team id like T0123ABCD")
    user_only = bool(raw.get("user_only", False))
    raw_ids = raw.get("identities") or {}
    _need(isinstance(raw_ids, dict) and raw_ids, f"{where}.identities must name at least one of {list(IDENTITIES)}")
    identities: dict[str, IdentityConfig] = {}
    for name, spec in raw_ids.items():
        _need(name in IDENTITIES, f"{where}.identities.{name}: unknown identity (use bot or user)")
        _need(isinstance(spec, dict) and isinstance(spec.get("token_env"), str), f"{where}.identities.{name}.token_env is required")
        default_policy = "free" if name == "bot" else "approval"
        policy = spec.get("write", default_policy)
        _need(policy in POLICIES, f"{where}.identities.{name}.write must be one of {list(POLICIES)}")
        identities[name] = IdentityConfig(name=name, token_env=spec["token_env"], write=policy)
    default_persona = raw.get("default_persona")
    if user_only:
        _need("bot" not in identities, f"{where} is user_only: it must not have a bot identity")
        _need(default_persona is None, f"{where} is user_only: personas do not apply")
        _need(identities["user"].write != "free", f"{where} is user_only: user writes must be 'approval' or 'never'")
    if default_persona is not None:
        _need("bot" in identities, f"{where}.default_persona needs a bot identity")
        _need(default_persona in personas, f"{where}.default_persona {default_persona!r} is not a defined persona")
    app_token_env = raw.get("app_token_env")
    intake = raw.get("intake", "socket" if app_token_env else "poll")
    _need(intake in INTAKES, f"{where}.intake must be one of {list(INTAKES)}")
    _need(intake != "socket" or bool(app_token_env), f"{where}: intake socket needs app_token_env")
    poll_seconds = float(raw.get("poll_seconds", 60))
    _need(poll_seconds >= 10, f"{where}.poll_seconds must be >= 10")
    sinks = tuple(_parse_sink(label, i, s, env) for i, s in enumerate(raw.get("sinks") or []))
    ack = raw.get("ack_reaction", "eyes" if "bot" in identities else None)
    if user_only:
        ack = None
    app_id = raw.get("app_id")
    _need(app_id is None or (isinstance(app_id, str) and bool(re.fullmatch(r"A[0-9A-Z]{6,}", app_id))), f"{where}.app_id must be a Slack app id like A0123ABCD")
    thread_cap = raw.get("bot_thread_posts_per_hour", 20)
    _need(isinstance(thread_cap, int) and not isinstance(thread_cap, bool) and thread_cap >= 1, f"{where}.bot_thread_posts_per_hour must be an integer >= 1")
    return WorkspaceConfig(
        label=label,
        team_id=team_id,
        user_only=user_only,
        app_token_env=app_token_env,
        identities=identities,
        watched=_str_list(raw.get("watched"), f"{where}.watched"),
        sinks=sinks,
        intake=intake,
        poll_seconds=poll_seconds,
        default_persona=default_persona,
        ack_reaction=ack or None,
        skip_user_ids=frozenset(_str_list(raw.get("skip_user_ids"), f"{where}.skip_user_ids")),
        app_id=app_id,
        bot_thread_posts_per_hour=thread_cap,
    )


def _parse_grant(key_id: str, raw: Any, workspaces: Mapping[str, WorkspaceConfig], personas: Mapping[str, Persona]) -> Grant:
    where = f"agents.{key_id}"
    _need(isinstance(raw, dict), f"{where} must be a mapping")
    _need(bool(LABEL_RE.match(key_id)), f"agent name {key_id!r} must match {LABEL_RE.pattern}")
    persona = raw.get("persona")
    _need(persona is None or persona in personas, f"{where}.persona {persona!r} is not a defined persona")
    allowed = raw.get("allowed_personas")
    allowed_t = _str_list(allowed, f"{where}.allowed_personas") if allowed is not None else None
    for p in allowed_t or ():
        _need(p in personas, f"{where}.allowed_personas: {p!r} is not a defined persona")
    scopes_raw = raw.get("workspaces") or {}
    _need(isinstance(scopes_raw, dict), f"{where}.workspaces must map workspace label -> [identities]")
    scopes: dict[str, tuple[str, ...]] = {}
    for label, ids in scopes_raw.items():
        _need(label in workspaces, f"{where}.workspaces: unknown workspace {label!r}")
        ids_t = _str_list(ids, f"{where}.workspaces.{label}")
        for i in ids_t:
            _need(i in workspaces[label].identities, f"{where}.workspaces.{label}: workspace has no {i!r} identity")
        scopes[label] = ids_t
    return Grant(
        id=key_id,
        persona=persona,
        allow_persona_override=bool(raw.get("allow_persona_override", False)),
        allowed_personas=allowed_t,
        scopes=scopes,
    )


def parse_config(data: Any, env: Mapping[str, str]) -> Config:
    if data is None:
        return Config()
    _need(isinstance(data, dict), "config root must be a mapping")
    public_base_url = data.get("public_base_url")
    if public_base_url is not None:
        _need(isinstance(public_base_url, str) and public_base_url.startswith("https://"), "public_base_url must be an https URL")
        public_base_url = public_base_url.rstrip("/")
    personas = {pid: _parse_persona(pid, raw, public_base_url) for pid, raw in (data.get("personas") or {}).items()}
    workspaces = {label: _parse_workspace(label, raw, env, personas) for label, raw in (data.get("workspaces") or {}).items()}
    _need("api_keys" not in data, "api_keys became agents: {<agent name>: {persona, workspaces, ...}}; switchboard's own keys authenticate")
    grants = {str(k).lower(): _parse_grant(str(k).lower(), raw, workspaces, personas) for k, raw in (data.get("agents") or {}).items()}
    appr = data.get("approvals") or {}
    approvals = ApprovalConfig(
        ttl_minutes=int(appr.get("ttl_minutes", 60)),
        notify_workspace=appr.get("notify_workspace"),
        approver_user_id=appr.get("approver_user_id"),
    )
    _need(1 <= approvals.ttl_minutes <= 7 * 24 * 60, "approvals.ttl_minutes must be 1..10080")
    if approvals.notify_workspace is not None:
        ws = workspaces.get(approvals.notify_workspace)
        _need(ws is not None and ws.has_bot, "approvals.notify_workspace must be a workspace with a bot identity")
        _need(bool(approvals.approver_user_id), "approvals.approver_user_id is required with notify_workspace")
    _need("access" not in data, "access is switchboard's auth.cloudflare_access now; drop it from the slack section")
    return Config(
        workspaces=workspaces,
        personas=personas,
        grants=grants,
        approvals=approvals,
        public_base_url=public_base_url,
        max_text_chars=int(data.get("max_text_chars", 4000)),
        event_retention_days=int(data.get("event_retention_days", 14)),
    )
