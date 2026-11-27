from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class CompanionError(ValueError):
    pass


def base_url(value: str) -> str:
    import httpx

    from ..config import LOOPBACK_HOSTS

    error = CompanionError("Use an HTTPS SwB URL or a loopback HTTP URL.")
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        httpx.URL(value)
    except (ValueError, httpx.InvalidURL):
        raise error from None
    if (not host or port == 0 or parsed.username is not None or re.search(r"[\x00-\x20\x7f\\?#]", value)
            or parsed.path not in ("/", "")
            or not (parsed.scheme == "https" or (parsed.scheme == "http" and host in LOOPBACK_HOSTS))):
        raise error
    return parsed._replace(path="").geturl()


@dataclass(frozen=True)
class CompanionState:
    url: str = field(repr=False)
    companion_id: str
    device_key: str = field(repr=False)
    enabled_caps: tuple[str, ...]
    drop_dir: str
    mcp_servers: tuple[str, ...] = field(default=(), repr=False)
    name: str | None = None

    @classmethod
    def load(cls, path: Path) -> CompanionState | None:
        try:
            parent = path.parent.lstat()
            if not stat.S_ISDIR(parent.st_mode) or _is_reparse(parent):
                raise CompanionError("Check the companion settings directory.")
            previous = path.lstat()
            if not stat.S_ISREG(previous.st_mode) or _is_reparse(previous):
                raise CompanionError("Check the companion settings file.")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            with os.fdopen(os.open(path, flags), encoding="utf-8") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or not os.path.samestat(previous, info)
                        or info.st_size > 65_536
                        or (os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077))):
                    raise CompanionError("Make the companion settings file private.")
                data = json.load(stream)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, RecursionError):
            raise CompanionError("Check the companion settings file and its permissions.") from None
        try:
            url, companion_id, key, caps, drop_dir = (
                data["url"], data["companion_id"], data["device_key"], data["enabled_caps"], data["drop_dir"]
            )
            if (not isinstance(url, str) or not valid_pair(companion_id, key)
                    or not isinstance(caps, list) or not all(isinstance(cap, str) for cap in caps)
                    or not isinstance(drop_dir, str) or not Path(drop_dir).is_absolute()):
                raise ValueError
            servers = data.get("mcp_servers", [])
            if not isinstance(servers, list) or not all(isinstance(server, str) for server in servers):
                raise ValueError
            specs = tuple(servers)
            name = data.get("name")
            if name is not None:
                name = device_name(name)
            from .mcp_config import local_servers

            if ("mcp" in caps) != bool(local_servers(specs)):
                raise ValueError
            return cls(base_url(url), companion_id, key, tuple(caps), drop_dir, specs, name)
        except (TypeError, ValueError, KeyError):
            raise CompanionError("Check the companion settings file.") from None

    def save(self, path: Path) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or _is_reparse(info)
                or (os.name != "nt" and info.st_uid != os.getuid())):
            raise CompanionError("Choose a private companion settings directory.")
        if os.name != "nt":
            path.parent.chmod(0o700)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                json.dump(asdict(self), stream, ensure_ascii=False)
                stream.close()
                temporary.chmod(0o600)
                if os.name == "nt":
                    temporary.rename(path)
                else:
                    path.hardlink_to(temporary)
            except FileExistsError:
                raise CompanionError("Disconnect this device before pairing again.") from None
            finally:
                temporary.unlink(missing_ok=True)

    def clear(self, path: Path) -> bool:
        current = self.load(path)
        if current is None:
            return True
        if (current.url, current.companion_id, current.device_key) != (self.url, self.companion_id, self.device_key):
            return False
        path.unlink(missing_ok=True)
        return True


def device_name(value: object) -> str:
    if isinstance(value, str) and (name := value.strip()) and len(name) <= 120 and name.isprintable():
        return name
    raise CompanionError("Enter a device name of up to 120 printable characters.")


def valid_pair(companion_id: object, key: object) -> bool:
    return (
        isinstance(companion_id, str) and re.fullmatch(r"cmp_[0-9a-f]{24}", companion_id) is not None
        and isinstance(key, str) and re.fullmatch(r"swbd_[A-Za-z0-9_-]{43}", key) is not None
    )


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def state_path() -> Path:
    if override := os.environ.get("SWB_COMPANION_HOME"):
        home = Path(override).expanduser()
    elif os.name == "nt":
        home = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")) / "swb"
    else:
        home = Path.home() / ".config" / "swb"
    return home / "companion.json"
