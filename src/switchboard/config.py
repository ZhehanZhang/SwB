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

DEFAULT_REDIRECTS = [
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://chatgpt.com/connector/oauth/*",
    "http://localhost/*",
    "http://127.0.0.1/*",
]
DEFAULT_CIMD_HOSTS = ["claude.ai", "claude.com", "anthropic.com", "chatgpt.com", "openai.com"]

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
    """Core settings plus the raw sections each origin and destination reads for itself."""

    public_url: str | None = None
    owner_emails: tuple[str, ...] = ()
    access_team_domain: str | None = None
    access_aud: str | None = None
    redirect_allow: tuple[str, ...] = tuple(DEFAULT_REDIRECTS)
    cimd_hosts: tuple[str, ...] = tuple(DEFAULT_CIMD_HOSTS)
    access_token_ttl: int = 3600
    refresh_token_ttl: int = 30 * 86400
    static_keys: tuple[StaticKey, ...] = ()
    source_names: dict[str, str] = field(default_factory=dict)
    key_scopes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    timezone: str = "America/New_York"
    mirror_dir: str | None = None
    mirror_debounce_s: float = 2.0
    origins: dict[str, dict[str, Any]] = field(default_factory=lambda: {"mcp": {}})
    destinations: dict[str, dict[str, Any]] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)

    @property
    def issuer(self) -> str | None:
        return self.public_url

    @property
    def resource(self) -> str | None:
        return f"{self.public_url}/mcp" if self.public_url else None

    def problems(self) -> list[str]:
        out = []
        if not self.public_url:
            out.append("public_url is not set in config: OAuth sign-in is disabled (static keys still work)")
        if not (self.access_team_domain and self.access_aud):
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

    origins = _sections(raw.get("origins"), "origins") if "origins" in raw else {"mcp": {}}
    cfg = Config(
        public_url=public_url,
        owner_emails=tuple(e.lower() for e in as_tuple(auth.get("owner_emails"))),
        access_team_domain=access.get("team_domain") or None,
        access_aud=access.get("aud") or None,
        redirect_allow=as_tuple(auth.get("redirect_uris")) or tuple(DEFAULT_REDIRECTS),
        cimd_hosts=as_tuple(auth.get("cimd_hosts")) or tuple(DEFAULT_CIMD_HOSTS),
        access_token_ttl=int(auth.get("access_token_ttl", 3600)),
        refresh_token_ttl=int(auth.get("refresh_token_ttl", 30 * 86400)),
        static_keys=tuple(keys),
        source_names={str(k): str(v) for k, v in (auth.get("source_names") or {}).items()},
        key_scopes={str(k).lower(): tuple(str(s).strip().lower() for s in as_tuple(v)) for k, v in (auth.get("key_scopes") or {}).items()},
        timezone=str(raw.get("timezone", "America/New_York")),
        mirror_dir=(memory.get("mirror_dir") or None) if memory.get("mirror", True) else None,
        mirror_debounce_s=float(memory.get("mirror_debounce_s", 2.0)),
        origins=origins,
        destinations=_sections(raw.get("destinations"), "destinations"),
        env=env,
    )
    if bool(cfg.access_team_domain) != bool(cfg.access_aud):
        raise ConfigError("cloudflare_access needs both team_domain and aud")
    if cfg.access_team_domain and not cfg.owner_emails:
        raise ConfigError("cloudflare_access needs auth.owner_emails")
    return cfg
