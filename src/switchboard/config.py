from __future__ import annotations

import base64
import hashlib
import os
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping
from urllib.parse import urlsplit

import yaml

from .block_env import configured_env, default_blocks, env_blocks
from .env import canonical_env_name, env_value

if TYPE_CHECKING:
    from .modes import ModeConfig

KEY_ENV_PREFIX = "SWB_KEY_"
MIN_KEY_LEN = 24
INSTANCE_CONFIG = Path("config/switchboard.yaml")
INSTANCE_DB = "switchboard.db"
CONFIG_PATHS = (INSTANCE_CONFIG, Path("/config/switchboard.yaml"))
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = "8870"
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
MCP_OFF = "MCP is off."

# https://www.cloudflare.com/ips-v4 and https://www.cloudflare.com/ips-v6 (2026-11-27)
CLOUDFLARE_NETWORKS = (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
    "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
    "2a06:98c0::/29", "2c0f:f248::/32",
)

# Keys that only read one agent's inbox (GET /inbox/poll and /inbox/events) and are refused on /mcp. The Claude Code
# channel uses SWB_KEY_CHANNEL to push what Claude Code's own OAuth sign-in (agent claude-code) receives.
DEFAULT_INBOX_KEYS = {"channel": "claude-code"}

REQUEST_BASE: ContextVar[str | None] = ContextVar("switchboard_request_base", default=None)


def _http_origin(host: str, port: int | str) -> str:
    netloc = f"[{host}]" if ":" in host else host
    return f"http://{netloc}:{port}"


def bind_address(env: Mapping[str, str], port: int | str, *, host: str | None = None) -> tuple[str, str]:
    host = host or env_value(env, "SWB_HOST", env.get("SWB_DEFAULT_HOST")) or DEFAULT_HOST
    origin_host = {"0.0.0.0": DEFAULT_HOST, "::": "::1"}.get(host, host)
    return host, _http_origin(origin_host, port)


def client_endpoint(env: Mapping[str, str]) -> str:
    host = env_value(env, "SWB_HOST")
    if host is None or host not in (DEFAULT_HOST, "::1"):
        host = "localhost"
    port = env_value(env, "SWB_PORT") or env.get("PORT") or DEFAULT_PORT
    return f"{_http_origin(host, port)}/mcp"


def local_origin(host: str, origin: str, env: Mapping[str, str]) -> str | None:
    if host in LOOPBACK_HOSTS:
        return origin
    compose_origin = env.get("SWB_COMPOSE_ORIGIN")
    if compose_origin and _web_url(compose_origin, local=True):
        parts = urlsplit(compose_origin)
        if parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS and parts.port and not parts.path:
            return compose_origin
    return None


def _origin(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    host = f"[{host}]" if ":" in host else host.encode("idna").decode()
    port = parts.port
    return f"{parts.scheme}://{host}" + (f":{port}" if port and port != {"https": 443, "http": 80}[parts.scheme] else "")


def _host_port(url: str, scheme: str | None = None) -> tuple[str, int] | None:
    try:
        parts = urlsplit(url)
        return (parts.hostname or "", parts.port or {"https": 443, "http": 80}[scheme or parts.scheme])
    except (ValueError, KeyError):
        return None


NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}(/[a-z0-9][a-z0-9_.-]{0,59}){0,2}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Instance:
    base: Path
    explicit: bool = False
    bare_home: bool = True


@dataclass(frozen=True)
class StaticKey:
    name: str
    secret: str = field(repr=False)


@dataclass(frozen=True)
class Branding:
    display_name: str = "SwB"
    public_identity: str = "swb"
    icon: str | None = None
    accent_color: str = "#64748b"
    logo_assets_dir: str | None = None


@dataclass(frozen=True)
class OIDCConfig:
    issuer: str
    client_id: str
    client_secret_env: str
    name: str
    owner_subjects: tuple[str, ...]


@dataclass
class Config:
    """Core settings plus the raw section each block reads for itself (blocks.<name>, with use: <block type>).

    redirect_allow and cimd_hosts are empty unless the config overrides them: then the agents registry decides.
    """

    public_url: str | None = None
    data_dir: Path = Path("data")
    legacy_instance_data: Path | None = None
    branding: Branding = Branding()
    public_url_aliases: tuple[str, ...] = ()
    passkey_rp_id: str | None = None
    owner_emails: tuple[str, ...] = ()
    access_team_domain: str | None = None
    access_aud: str | None = None
    trusted_proxies: tuple[IPv4Network | IPv6Network, ...] = ()
    oidc: OIDCConfig | None = None
    owner_setup_digest: bytes | None = field(default=None, repr=False)
    redirect_allow: tuple[str, ...] = ()
    cimd_hosts: tuple[str, ...] = ()
    access_token_ttl: int = 3600
    refresh_token_ttl: int = 30 * 86400
    static_keys: tuple[StaticKey, ...] = ()
    source_names: dict[str, str] = field(default_factory=dict)
    key_scopes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    key_tools: dict[str, tuple[str, ...]] = field(default_factory=dict)
    key_targets: dict[str, tuple[str, ...]] = field(default_factory=dict)
    inbox_keys: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_INBOX_KEYS))
    timezone: str = "UTC"
    turn_cap: int | None = 200
    mirror_dir: str | None = None
    mirror_debounce_s: float = 2.0
    backup_debounce_s: float = 30.0
    backup_max_delay_s: float = 300.0
    blocks: dict[str, dict[str, Any]] = field(default_factory=default_blocks)
    blocks_from_file: bool = False
    block_env: dict[str, tuple[str, ...]] = field(default_factory=dict)
    agents: dict[str, Any] | None = None
    targets: dict[str, Any] = field(default_factory=dict)
    routes: list[dict[str, Any]] = field(default_factory=list)
    visibility: dict[str, Any] = field(default_factory=dict)
    modes: ModeConfig | None = None
    version: int = 2
    env: Mapping[str, str] = field(default_factory=dict, repr=False)

    @property
    def public_urls(self) -> tuple[str, ...]:
        return (self.public_url, *self.public_url_aliases) if self.public_url else ()

    def base_for_host(self, host: str | None) -> str | None:
        """The public URL a request reached, by its Host header; the primary public_url for any other host."""
        if host:
            want = _host_port(f"//{host}", "https")
            for url in self.public_urls:
                if _host_port(url) == want or _host_port(url) == _host_port(f"//{host}", "http"):
                    return url
        return self.public_url

    @property
    def issuer(self) -> str | None:
        """The base URL of the current request (one of public_urls), so each host is its own consistent issuer."""
        base = REQUEST_BASE.get()
        return base if base in self.public_urls else self.public_url

    @property
    def resource(self) -> str | None:
        return f"{self.issuer}/mcp" if self.issuer else None

    @property
    def resources(self) -> tuple[str, ...]:
        return tuple(f"{url}/mcp" for url in self.public_urls)

    @property
    def legacy_passkey_rp(self) -> str | None:
        """Passkeys saved before RP IDs were recorded belong to the primary host."""
        return urlsplit(self.public_url).hostname if self.public_url else None

    @property
    def passkey_rp(self) -> str | None:
        return self.passkey_rp_id or (urlsplit(self.issuer).hostname if self.issuer else None)

    @property
    def access_configured(self) -> bool:
        return bool(self.access_team_domain and self.access_aud and self.owner_emails)

    @property
    def owner_sign_in(self) -> bool:
        return bool(self.public_url or self.access_configured or self.oidc)

    @property
    def public_origin(self) -> str | None:
        return _origin(self.issuer) if self.issuer else None

    @property
    def mcp_enabled(self) -> bool:
        return any((settings.get("use") or name) == "mcp" for name, settings in self.blocks.items())

    def problems(self) -> list[str]:
        out = []
        if not self.public_url:
            out.append("Set SWB_PUBLIC_URL to enable OAuth sign-in")
        if not self.owner_sign_in:
            out.append("Set SWB_PUBLIC_URL or configure an upstream method for owner sign-in")
        if not self.mcp_enabled:
            out.append(MCP_OFF)
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
        out[str(name)] = sec
    return out


def agent_name(key: Any) -> str:
    """A bearer key's agent name, as its env suffix and the auth maps both spell it: lowercase, with _ as -."""
    return str(key).strip().lower().replace("_", "-")


def _web_url(value: str, *, local: bool = False) -> bool:
    try:
        url = urlsplit(value)
        return bool(url.hostname and url.port != 0 and url.username is None and not re.search(r"[\s?#\\]", value)
                    and (url.scheme == "https" or (local and url.scheme == "http" and url.hostname in LOOPBACK_HOSTS)))
    except ValueError:
        return False


def _oidc(raw: Any, public_url: str | None, emails: tuple[str, ...], env: Mapping[str, str]) -> OIDCConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("auth.oidc must be a mapping")
    if not public_url:
        raise ConfigError("auth.oidc needs public_url")
    fields = ("issuer", "client_id", "client_secret_env", "name")
    values = [raw.get(field) for field in fields]
    for field_name, value in zip(fields, values):
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"auth.oidc.{field_name} must be a non-empty string")
    issuer, client_id, secret_env, name = values
    if not _web_url(issuer):
        raise ConfigError("auth.oidc.issuer must be an HTTPS URL without a query or fragment")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", secret_env) or not env_value(env, secret_env):
        raise ConfigError("auth.oidc.client_secret_env must name a set environment variable")
    subjects = raw.get("owner_subjects", [])
    if not isinstance(subjects, list) or not all(isinstance(s, str) and s.strip() for s in subjects):
        raise ConfigError("auth.oidc.owner_subjects must be a list of non-empty strings")
    if not subjects and not emails:
        raise ConfigError("auth.oidc needs owner_subjects or auth.owner_emails")
    return OIDCConfig(issuer, client_id, secret_env, name, tuple(subjects))


def config_path(path: str | Path | None, env: Mapping[str, str], *, instance: Instance | None = None) -> Path | None:
    if instance is not None:
        local = instance.base / INSTANCE_CONFIG
        if instance.explicit or (not path and local.is_file() and not env_value(env, "SWB_CONFIG")):
            path = local
    path = path or env_value(env, "SWB_CONFIG")
    return Path(path) if path else next((p for p in CONFIG_PATHS if p.is_file()), None)


def _read_yaml(path: Path | None, what: str, *, required: bool = False) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise ConfigError(f"{what} file not found") from None
        return None
    except (OSError, UnicodeError, yaml.YAMLError):
        raise ConfigError(f"could not read {what} as YAML") from None
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{what} root must be a mapping")
    return raw


def _owner_setup_digest(code: str | None) -> bytes | None:
    if code is None:
        return None
    if re.fullmatch(r"[0-9a-f]{64}", code):
        return hashlib.sha256(code.encode()).digest()
    if re.fullmatch(r"[A-Za-z0-9+/]{43}=", code) and base64.b64encode(base64.b64decode(code)).decode() == code:
        return hashlib.sha256(code.encode()).digest()
    raise ConfigError("SWB_SETUP_CODE must encode 32 random bytes as lowercase hexadecimal or standard Base64.")


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    return {**base, **{key: _merge(base[key], value) if isinstance(value, dict) and isinstance(base.get(key), dict) else value
                      for key, value in overlay.items()}}


def _config_files(path: str | Path | None, env: Mapping[str, str],
                  instance: Instance | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    base_path = config_path(path, env, instance=instance)
    base = _read_yaml(base_path, "config")
    overlay_path = env_value(env, "SWB_OVERLAY")
    local_paths = (p.with_name("local.yaml") for p in ((base_path,) if base_path else CONFIG_PATHS))
    local_path = Path(overlay_path) if overlay_path else next((p for p in local_paths if p.is_file()), None)
    overlay = _read_yaml(local_path, "overlay", required=bool(overlay_path)) or {}
    return base, overlay


def configured_data_dir(env: Mapping[str, str]) -> str | None:
    return (env.get("SWB_DATA_DIR") or env.get("SWB_DATA")
            or env_value(env, "SWB_DATA_DIR") or env_value(env, "SWB_DATA") or env.get("SWB_DEFAULT_DATA_DIR"))


def _data_paths(env: Mapping[str, str], raw: Mapping[str, Any], instance: Instance | None, *,
                has_config: bool) -> tuple[Path, Path | None]:
    if configured := raw.get("data_dir") or configured_data_dir(env):
        return Path(configured).expanduser(), None
    if instance is None:
        return Path("data"), None
    home_data = Path.home() / ".local/share/switchboard"
    if not instance.explicit and not has_config:
        return (home_data if instance.bare_home else Path("data")), None
    data = instance.base / "data"
    if not instance.explicit and not os.path.lexists(data) and (home_data / INSTANCE_DB).is_file():
        return home_data, data
    return data, None


def instance_data_dir(env: Mapping[str, str], instance: Instance) -> Path:
    base, overlay = _config_files(None, env, instance)
    return _data_paths(env, _merge(base or {}, overlay), instance, has_config=base is not None)[0]


def _branding(raw: Any) -> Branding:
    if raw is None:
        return Branding()
    if not isinstance(raw, dict):
        raise ConfigError("branding must be a mapping")
    for name in ("display_name", "icon", "accent_color", "logo_assets_dir"):
        if name in raw and raw[name] is not None and (not isinstance(raw[name], str) or not raw[name].strip()):
            raise ConfigError(f"branding.{name} must be a nonempty string")
    accent = raw.get("accent_color") or Branding.accent_color
    if not re.fullmatch(r"#[a-fA-F0-9]{6}", accent):
        raise ConfigError("branding.accent_color must be a #RRGGBB color")
    return Branding(display_name=raw.get("display_name") or Branding.display_name, accent_color=accent,
                    icon=raw.get("icon"), logo_assets_dir=raw.get("logo_assets_dir"))


def _trusted_proxies(raw: Any) -> tuple[IPv4Network | IPv6Network, ...]:
    if not isinstance(raw, list) or not all(isinstance(entry, str) for entry in raw):
        raise ConfigError("Set trusted_proxies to a list of IP ranges.")
    networks = []
    for entry in raw:
        entry = entry.strip()
        try:
            networks.extend(ip_network(value) for value in (CLOUDFLARE_NETWORKS if entry == "cloudflare" else (entry,)))
        except ValueError:
            raise ConfigError("Use IP ranges or cloudflare in trusted_proxies.") from None
    return tuple(dict.fromkeys(networks))


def load_config(path: str | Path | None = None, env: Mapping[str, str] | None = None, *,
                tunnel_url: str | None = None, loopback_origin: str | None = None,
                instance: Instance | None = None) -> Config:
    from .modes import parse_config as parse_modes

    env = dict(os.environ if env is None else env)
    owner_setup_digest = _owner_setup_digest(env.pop("SWB_SETUP_CODE", None))
    base, overlay = _config_files(path, env, instance)
    raw = _merge(base or {}, overlay)
    data_dir, legacy_instance_data = _data_paths(env, raw, instance, has_config=base is not None)
    auth = raw.get("auth") or {}
    if not isinstance(auth, dict):
        raise ConfigError("auth must be a mapping")
    access = auth.get("cloudflare_access", {})
    access_env = env if access is not None else {}
    access = access or {}
    if not isinstance(access, dict):
        raise ConfigError("auth.cloudflare_access must be a mapping")
    memory = raw.get("memory") or {}
    backup = memory.get("backup") or {}
    if not isinstance(backup, dict):
        raise ConfigError("memory.backup must be a mapping")
    debounce = backup.get("debounce_s", 30)
    max_delay = backup.get("max_delay_s", 300)
    if any(type(delay) not in (float, int) or not 0 <= delay <= 86400 for delay in (debounce, max_delay)):
        raise ConfigError("Set memory.backup delays to seconds between 0 and 86400.")
    proxies = raw["trusted_proxies"] if "trusted_proxies" in raw else [
        entry for entry in (env_value(env, "SWB_TRUSTED_PROXIES") or "").split(",") if entry.strip()
    ]

    configured_url = tunnel_url or (raw["public_url"] if "public_url" in raw
                                    else env_value(env, "SWB_PUBLIC_URL") or env.get("PUBLIC_URL"))
    if isinstance(configured_url, str):
        configured_url = configured_url.strip().rstrip("/") or None
    public_url = configured_url
    if public_url is None and loopback_origin is not None:
        public_url = f"http://localhost:{urlsplit(loopback_origin).port}"
    if public_url is not None and (not isinstance(public_url, str) or not _web_url(public_url, local=True)):
        raise ConfigError("public_url must be an HTTPS URL or use a loopback address for HTTP")
    emails = auth["owner_emails"] if "owner_emails" in auth else (env_value(env, "SWB_OWNER_EMAILS") or "").split(",")
    owner_emails = tuple(e.strip().lower() for e in as_tuple(emails) if e.strip())
    aliases = tuple(str(u).strip().rstrip("/") for u in as_tuple(raw.get("public_url_aliases")))
    if configured_url is None and loopback_origin and loopback_origin != public_url and loopback_origin not in aliases:
        aliases = (*aliases, loopback_origin)
    if aliases and not public_url:
        raise ConfigError("public_url_aliases needs public_url")
    if not all(_web_url(u, local=True) and u != public_url for u in aliases):
        raise ConfigError("public_url_aliases must be other HTTPS or HTTP loopback URLs for this server")
    rp_id = str(auth.get("passkey_rp_id") or "").strip().lower() or None
    if rp_id is not None:
        hosts = [urlsplit(u).hostname or "" for u in (public_url, *aliases) if u]
        if not hosts or "." not in rp_id or not all(h == rp_id or h.endswith("." + rp_id) for h in hosts):
            raise ConfigError("auth.passkey_rp_id must be every public host or a parent domain they share")

    keys = []
    for name in sorted({canonical_env_name(name) for name in env}):
        if name.startswith(KEY_ENV_PREFIX) and (value := env_value(env, name)):
            if len(value) < MIN_KEY_LEN:
                raise ConfigError(f"{name} must be at least {MIN_KEY_LEN} characters")
            keys.append(StaticKey(name=agent_name(name[len(KEY_ENV_PREFIX):]), secret=value))

    legacy = any(k in raw for k in ("origins", "destinations", "slack"))
    version = int(raw.get("version", 2 if "blocks" in raw or (not base and not legacy) else 1) or 1)
    if version not in (1, 2):
        raise ConfigError("version must be 1 or 2")
    if version == 1:
        v2 = translate_v1(raw)
        if "blocks" in overlay:
            v2["blocks"] = _merge(v2["blocks"], overlay["blocks"]) if isinstance(overlay["blocks"], dict) else overlay["blocks"]
    elif base and int(base.get("version", 2 if "blocks" in base else 1) or 1) == 1:
        v2 = _merge(translate_v1(base), overlay)
    else:
        v2 = raw
    discovered, block_env = env_blocks(env)
    blocks_from_file = "blocks" in v2
    if blocks_from_file:
        sections = _sections(v2["blocks"], "blocks")
        defaults = default_blocks()
        for name, sec in sections.items():
            use = sec.get("use") or name
            if isinstance(use, str) and name != use:
                defaults.pop(use, None)
        sections = _merge(defaults, sections)
        blocks = {name: {"use": name, **sec} for name, sec in sections.items() if sec.get("enabled") is not False}
        for name in sections:
            block_env[name] = configured_env(name, blocks[name]) if name in blocks else block_env.get(name, ())
    else:
        blocks = discovered
    cfg = Config(
        visibility=dict(raw.get("visibility") or v2.get("visibility") or {}),
        public_url=public_url,
        data_dir=data_dir,
        legacy_instance_data=legacy_instance_data,
        branding=_branding(raw.get("branding")),
        public_url_aliases=aliases,
        passkey_rp_id=rp_id,
        owner_emails=owner_emails,
        access_team_domain=access.get("team_domain", access_env.get("CLOUDFLARE_ACCESS_TEAM_DOMAIN")) or None,
        access_aud=access.get("aud", access_env.get("CLOUDFLARE_ACCESS_AUD")) or None,
        trusted_proxies=_trusted_proxies(proxies),
        oidc=_oidc(auth.get("oidc"), public_url, owner_emails, env),
        owner_setup_digest=owner_setup_digest,
        redirect_allow=as_tuple(auth.get("redirect_uris")),
        cimd_hosts=as_tuple(auth.get("cimd_hosts")),
        access_token_ttl=int(auth.get("access_token_ttl", 3600)),
        refresh_token_ttl=int(auth.get("refresh_token_ttl", 30 * 86400)),
        static_keys=tuple(keys),
        source_names={str(k): str(v) for k, v in (auth.get("source_names") or {}).items()},
        key_scopes={agent_name(k): tuple(str(s).strip().lower() for s in as_tuple(v)) for k, v in (auth.get("key_scopes") or {}).items()},
        key_tools={agent_name(k): tuple(str(s).strip().lower() for s in as_tuple(v)) for k, v in (auth.get("key_tools") or {}).items()},
        key_targets={agent_name(k): tuple(str(s).strip() for s in as_tuple(v)) for k, v in (auth.get("key_targets") or {}).items()},
        inbox_keys={**DEFAULT_INBOX_KEYS, **{agent_name(k): str(v).lower() for k, v in (auth.get("inbox_keys") or {}).items()}},
        timezone=str(raw.get("timezone", "UTC")),
        turn_cap=int(raw.get("turn_cap", 200)) if raw.get("turn_cap", 200) else None,
        mirror_dir=(memory.get("mirror_dir") or None) if memory.get("mirror", True) else None,
        mirror_debounce_s=float(memory.get("mirror_debounce_s", 2.0)),
        backup_debounce_s=float(debounce),
        backup_max_delay_s=float(max_delay),
        blocks=blocks,
        blocks_from_file=blocks_from_file,
        modes=parse_modes(v2.get("modes"), blocks),
        block_env=block_env,
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
    if not any((sec.get("use") or name) == "mcp" for name, sec in blocks.items()):
        blocks.setdefault("mcp", {"use": "mcp", "enabled": False})
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
