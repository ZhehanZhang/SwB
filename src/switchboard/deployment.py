from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile

from .config import ConfigError

OWNER_KEY = "SWITCHBOARD_KEY_OWNER"
PLATFORM = "SWITCHBOARD_DEPLOY_PLATFORM"


def volume_owner_key(env: Mapping[str, str], *, create: bool = False) -> str | None:
    if not env.get(PLATFORM):
        return None
    data = env.get("SWITCHBOARD_DATA_DIR") or env.get("SWITCHBOARD_DATA")
    if not data:
        raise ConfigError("Set SWITCHBOARD_DATA to the persistent volume mount.")
    path = Path(data) / "owner-key"
    if create and not path.exists():
        with NamedTemporaryFile(mode="w", encoding="ascii", dir=path.parent) as key:
            key.write(secrets.token_hex(32) + "\n")
            key.flush()
            os.fsync(key.fileno())
            try:
                path.hardlink_to(key.name)
            except FileExistsError:
                pass
    try:
        value = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ConfigError("The owner key file on the volume is invalid.")
    return value


def serve_env(env: Mapping[str, str]) -> dict[str, str] | None:
    if not (platform := env.get(PLATFORM)):
        return None
    values = dict(env)
    if not (values.get("SWITCHBOARD_PUBLIC_URL") or values.get("PUBLIC_URL")):
        url = None
        if platform == "render":
            url = values.get("RENDER_EXTERNAL_URL")
        elif platform == "fly" and (app := values.get("FLY_APP_NAME")):
            url = f"https://{app}.fly.dev"
        elif platform == "koyeb" and (host := values.get("KOYEB_PUBLIC_DOMAIN")):
            url = f"https://{host}"
        elif platform == "railway" and (host := values.get("RAILWAY_PUBLIC_DOMAIN")):
            url = f"https://{host}"
        if not url:
            raise ConfigError("The deployment requires a public URL.")
        values["SWITCHBOARD_PUBLIC_URL"] = url
    if not values.get(OWNER_KEY):
        key = volume_owner_key(values, create=True)
        if key is None:
            raise ConfigError("The deployment requires an owner key.")
        values[OWNER_KEY] = key
    return values
