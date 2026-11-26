from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

ENV_PREFIX = "SWITCHBOARD_"
KEY_ENV_PREFIX = "SWITCHBOARD_KEY_"
MIN_KEY_LEN = 24

# Keys that only read one agent's inbox (GET /inbox/poll and /inbox/events) and are refused on /mcp. The Claude Code
# channel uses SWITCHBOARD_KEY_CHANNEL to push what Claude Code's own OAuth sign-in (agent claude-code) receives.
DEFAULT_INBOX_KEYS = {"channel": "claude-code"}

NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}(/[a-z0-9][a-z0-9_.-]{0,59}){0,2}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class StaticKey:
    name: str
    secret: str


@dataclass
class Config:
    """Core settings plus the raw section each block reads for itself (blocks.<name>, with use: <block type>).

    redirect_allow and cimd_hosts are empty unless the config overrides them: then the agents registry decides.
    """

    public_url: str | None = None
    owner_emails: tuple[str, ...] = ()
    access_team_domain: str | None = None
    access_aud: str | None = None
    redirect_allow: tuple[str, ...] = ()
    cimd_hosts: tuple[str, ...] = ()
    access_token_ttl: int = 3600
    refresh_token_ttl: int = 30 * 86400
    static_keys: tuple[StaticKey, ...] = ()
    source_names: dict[str, str] = field(default_factory=dict)
    key_scopes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    inbox_keys: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_INBOX_KEYS))
    timezone: str = "America/New_York"
    turn_cap: int | None = 200
    mirror_dir: str | None = None
    mirror_debounce_s: float = 2.0
    blocks: dict[str, dict[str, Any]] = field(default_factory=lambda: {"mcp": {"use": "mcp"}})
    agents: dict[str, Any] | None = None
    targets: dict[str, Any] = field(default_factory=dict)
    routes: list[dict[str, Any]] = field(default_factory=list)
    visibility: dict[str, Any] = field(default_factory=dict)
    version: int = 2
    env: Mapping[str, str] = field(default_factory=dict)

    @property
    def issuer(self) -> str | None:
        return self.public_url

    @property
    def resource(self) -> str | None:
        return f"{self.public_url}/mcp" if self.public_url else None

    @property
    def owner_sign_in(self) -> bool:
        return bool(self.access_team_domain and self.access_aud and self.owner_emails)

    def problems(self) -> list[str]:
        out = []
        if not self.public_url:
            out.append("public_url is not set in config: OAuth sign-in is disabled (static keys still work)")
        if not self.owner_sign_in:
            out.append("auth.cloudflare_access is not set: OAuth sign-in and the admin page are off (bearer keys still work)")
        return out


def as_tuple(v: Any) -> tuple[str, ...]:
    if v is None:
        return ()
    if isinstance(v, str):
        return (v,)
    return tuple(str(x) for x in v)


def _sections(raw: Any, what: str) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{what} must be a mapping of name -> settings")
    out = {}
    for name, sec in raw.items():
        sec = sec or {}
        if not isinstance(sec, dict):
            raise ConfigError(f"{what}.{name} must be a mapping")
        if sec.get("enabled", True):
            out[str(name)] = sec
    return out


def load_config(path: str | Path | None, env: Mapping[str, str] | None = None) -> Config:
    env = dict(os.environ if env is None else env)
    raw: dict[str, Any] = {}
    if path and Path(path).exists():
        raw = yaml.safe_load(Path(path).read_text()) or {}
        if not isinstance(raw, dict):
            raise ConfigError("config root must be a mapping")
    auth = raw.get("auth") or {}
    access = auth.get("cloudflare_access") or {}
    memory = raw.get("memory") or {}

    public_url = (env.get(ENV_PREFIX + "PUBLIC_URL") or raw.get("public_url") or "").strip().rstrip("/") or None
    if public_url and not public_url.startswith("https://") and not public_url.startswith("http://localhost"):
        raise ConfigError("public_url must be https")

    keys = []
    for name, value in sorted(env.items()):
        if name.startswith(KEY_ENV_PREFIX) and value:
            if len(value) < MIN_KEY_LEN:
                raise ConfigError(f"{name} must be at least {MIN_KEY_LEN} characters")
            keys.append(StaticKey(name=name[len(KEY_ENV_PREFIX):].lower(), secret=value))

    version = int(raw.get("version", 1) or 1)
    if version not in (1, 2):
        raise ConfigError("version must be 1 or 2")
    v2 = raw if version == 2 else translate_v1(raw)
    blocks = _sections(v2.get("blocks"), "blocks") if "blocks" in v2 else {"mcp": {"use": "mcp"}}
    for name, sec in blocks.items():
        sec.setdefault("use", name)
    cfg = Config(
        visibility=dict(raw.get("visibility") or v2.get("visibility") or {}),
        public_url=public_url,
        owner_emails=tuple(e.lower() for e in as_tuple(auth.get("owner_emails"))),
        access_team_domain=access.get("team_domain") or None,
        access_aud=access.get("aud") or None,
        redirect_allow=as_tuple(auth.get("redirect_uris")),
        cimd_hosts=as_tuple(auth.get("cimd_hosts")),
        access_token_ttl=int(auth.get("access_token_ttl", 3600)),
        refresh_token_ttl=int(auth.get("refresh_token_ttl", 30 * 86400)),
        static_keys=tuple(keys),
        source_names={str(k): str(v) for k, v in (auth.get("source_names") or {}).items()},
        key_scopes={str(k).lower(): tuple(str(s).strip().lower() for s in as_tuple(v)) for k, v in (auth.get("key_scopes") or {}).items()},
        inbox_keys={**DEFAULT_INBOX_KEYS, **{str(k).lower(): str(v).lower() for k, v in (auth.get("inbox_keys") or {}).items()}},
        timezone=str(raw.get("timezone", "America/New_York")),
        turn_cap=int(raw.get("turn_cap", 200)) if raw.get("turn_cap", 200) else None,
        mirror_dir=(memory.get("mirror_dir") or None) if memory.get("mirror", True) else None,
        mirror_debounce_s=float(memory.get("mirror_debounce_s", 2.0)),
        blocks=blocks,
        agents=dict(v2["agents"]) if isinstance(v2.get("agents"), dict) else None,
        targets=dict(v2.get("targets") or {}),
        routes=[dict(r) for r in v2.get("routes") or [] if isinstance(r, dict)],
        version=version,
        env=env,
    )
    if bool(cfg.access_team_domain) != bool(cfg.access_aud):
        raise ConfigError("cloudflare_access needs both team_domain and aud")
    if cfg.access_team_domain and not cfg.owner_emails:
        raise ConfigError("cloudflare_access needs auth.owner_emails")
    return cfg


def translate_v1(raw: Mapping[str, Any]) -> dict[str, Any]:
    """A version 1 config (origins:, destinations:, slack:) as version 2 (blocks:). Core keys carry over unchanged.

    origins.<x> and destinations.<x> of the same name merge into blocks.<x>; the slack: section becomes the slack
    block's settings, and origins.slack.workspaces.<label>.project becomes its workspace_projects.<label>.
    """
    out: dict[str, Any] = {k: v for k, v in raw.items() if k not in ("origins", "destinations", "slack", "version")}
    out["version"] = 2
    blocks: dict[str, dict[str, Any]] = {}
    origins = raw.get("origins") if "origins" in raw else {"mcp": {}}
    for what, section in (("origins", origins), ("destinations", raw.get("destinations"))):
        for name, sec in (section or {}).items():
            if not isinstance(sec, dict) and sec is not None:
                raise ConfigError(f"{what}.{name} must be a mapping")
            sec = dict(sec or {})
            if name == "slack" and what == "origins" and isinstance(sec.get("workspaces"), dict):
                sec["workspace_projects"] = {k: (v or {}).get("project") for k, v in sec.pop("workspaces").items() if (v or {}).get("project")}
            merged = blocks.setdefault(str(name), {"use": str(name)})
            if sec.get("enabled", True) is False:
                merged["enabled"] = False
            merged.update({k: v for k, v in sec.items() if k != "enabled"})
    if "slack" in blocks and "slack" not in raw:
        blocks["slack"]["engine"] = False
    if "slack" in raw:
        slack = blocks.setdefault("slack", {"use": "slack"})
        clash = sorted(set(slack) & set(raw.get("slack") or {}) - {"use"})
        if clash:
            raise ConfigError(f"slack settings {clash} appear in both the slack section and origins/destinations.slack")
        slack.update(dict(raw.get("slack") or {}))
    out["blocks"] = blocks
    return out


def migrate_text(text: str) -> str:
    """switchboard config migrate: a v1 YAML file rewritten as v2 (comments are not kept)."""
    raw = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")
    if int(raw.get("version", 1) or 1) == 2:
        return text
    return yaml.safe_dump(translate_v1(raw), sort_keys=False, allow_unicode=True)
