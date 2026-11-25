from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

KEY_ENV_PREFIX = "CAPY_BRIDGE_KEY_"
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


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Project:
    alias: str
    id: str
    repos: tuple[str, ...] = ()


@dataclass(frozen=True)
class StaticKey:
    name: str
    secret: str


@dataclass
class Config:
    public_url: str | None = None
    owner_emails: tuple[str, ...] = ()
    owner_password: str | None = None
    access_team_domain: str | None = None
    access_aud: str | None = None
    require_password: bool = True
    redirect_allow: tuple[str, ...] = tuple(DEFAULT_REDIRECTS)
    cimd_hosts: tuple[str, ...] = tuple(DEFAULT_CIMD_HOSTS)
    access_token_ttl: int = 3600
    refresh_token_ttl: int = 30 * 86400
    capy_api_base: str = "https://api.capy.ai/api/v1"
    capy_api_key: str | None = None
    captain_id: str | None = None
    default_target: str = "captain"
    captain_delivery: str = "queue"
    projects: dict[str, Project] = field(default_factory=dict)
    daily_cap: int = 20
    timezone: str = "America/New_York"
    max_file_bytes: int = 10 * 1024 * 1024
    mirror_dir: str | None = None
    static_keys: tuple[StaticKey, ...] = ()
    source_names: dict[str, str] = field(default_factory=dict)

    @property
    def issuer(self) -> str | None:
        return self.public_url

    @property
    def resource(self) -> str | None:
        return f"{self.public_url}/mcp" if self.public_url else None

    def problems(self) -> list[str]:
        out = []
        if not self.capy_api_key:
            out.append("CAPY_API_KEY is not set: capy_* offload tools are disabled (memory tools still work)")
        if not self.public_url:
            out.append("public_url is not set in config: OAuth sign-in for Claude/ChatGPT is disabled (static keys still work)")
        if not self.owner_password and not (self.access_team_domain and self.access_aud):
            out.append("no owner login factor: set CAPY_BRIDGE_OWNER_PASSWORD or cloudflare_access in config")
        elif self.require_password and not self.owner_password:
            out.append("require_password is true but CAPY_BRIDGE_OWNER_PASSWORD is not set: sign-in is disabled")
        if self.capy_api_key and self.default_target == "captain" and not self.captain_id:
            out.append("default_target is captain but capy.captain_id is not set")
        return out


def _tuple(v: Any) -> tuple[str, ...]:
    if v is None:
        return ()
    if isinstance(v, str):
        return (v,)
    return tuple(str(x) for x in v)


def load_config(path: str | Path | None, env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    raw: dict[str, Any] = {}
    if path and Path(path).exists():
        raw = yaml.safe_load(Path(path).read_text()) or {}
        if not isinstance(raw, dict):
            raise ConfigError("config root must be a mapping")
    auth = raw.get("auth") or {}
    access = auth.get("cloudflare_access") or {}
    capy = raw.get("capy") or {}
    delegate = raw.get("delegate") or {}
    memory = raw.get("memory") or {}

    public_url = (env.get("CAPY_BRIDGE_PUBLIC_URL") or raw.get("public_url") or "").strip().rstrip("/") or None
    if public_url and not public_url.startswith("https://") and not public_url.startswith("http://localhost"):
        raise ConfigError("public_url must be https")

    projects: dict[str, Project] = {}
    for alias, p in (delegate.get("projects") or {}).items():
        if not isinstance(p, dict) or not p.get("id"):
            raise ConfigError(f"delegate.projects.{alias} needs an id")
        projects[str(alias)] = Project(alias=str(alias), id=str(p["id"]), repos=tuple(r.lower() for r in _tuple(p.get("repos"))))

    keys = []
    for name, value in sorted(env.items()):
        if name.startswith(KEY_ENV_PREFIX) and value:
            if len(value) < MIN_KEY_LEN:
                raise ConfigError(f"{name} must be at least {MIN_KEY_LEN} characters")
            keys.append(StaticKey(name=name[len(KEY_ENV_PREFIX):].lower(), secret=value))

    cfg = Config(
        public_url=public_url,
        owner_emails=tuple(e.lower() for e in _tuple(auth.get("owner_emails"))),
        owner_password=env.get("CAPY_BRIDGE_OWNER_PASSWORD") or None,
        access_team_domain=(access.get("team_domain") or None),
        access_aud=(access.get("aud") or None),
        require_password=bool(auth.get("require_password", True)),
        redirect_allow=_tuple(auth.get("redirect_uris")) or tuple(DEFAULT_REDIRECTS),
        cimd_hosts=_tuple(auth.get("cimd_hosts")) or tuple(DEFAULT_CIMD_HOSTS),
        access_token_ttl=int(auth.get("access_token_ttl", 3600)),
        refresh_token_ttl=int(auth.get("refresh_token_ttl", 30 * 86400)),
        capy_api_base=str(capy.get("api_base", "https://api.capy.ai/api/v1")).rstrip("/"),
        capy_api_key=env.get("CAPY_API_KEY") or None,
        captain_id=capy.get("captain_id") or None,
        default_target=str(delegate.get("default_target", "captain")),
        captain_delivery=str(delegate.get("captain_delivery", "queue")),
        projects=projects,
        daily_cap=int(delegate.get("daily_cap", 20)),
        timezone=str(delegate.get("timezone", "America/New_York")),
        max_file_bytes=int(delegate.get("max_file_bytes", 10 * 1024 * 1024)),
        mirror_dir=memory.get("mirror_dir") or None,
        static_keys=tuple(keys),
        source_names={str(k): str(v) for k, v in (auth.get("source_names") or {}).items()},
    )
    if cfg.default_target != "captain" and cfg.default_target not in projects:
        raise ConfigError(f"default_target {cfg.default_target!r} is neither 'captain' nor a delegate.projects alias")
    if cfg.captain_delivery not in ("queue", "steer", "interrupt"):
        raise ConfigError("captain_delivery must be queue, steer or interrupt")
    if bool(cfg.access_team_domain) != bool(cfg.access_aud):
        raise ConfigError("cloudflare_access needs both team_domain and aud")
    if cfg.access_team_domain and not cfg.owner_emails:
        raise ConfigError("cloudflare_access needs auth.owner_emails")
    return cfg
