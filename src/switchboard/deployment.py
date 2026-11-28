from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile

from .config import ConfigError, Instance, configured_data_dir, instance_data_dir
from .env import env_value
from .store import ensure_private_data_dir

OWNER_KEY = "SWB_KEY_OWNER"
PLATFORM = "SWB_DEPLOY_PLATFORM"


def volume_owner_key(env: Mapping[str, str], *, create: bool = False, instance: Instance | None = None) -> str | None:
    if not env_value(env, PLATFORM):
        return None
    data = instance_data_dir(env, instance) if instance is not None else configured_data_dir(env)
    if not data:
        raise ConfigError("Set SWB_DATA_DIR to the persistent volume mount.")
    path = Path(data) / "owner-key"
    if create and not path.exists():
        if not path.parent.is_dir():
            ensure_private_data_dir(path.parent)
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


def serve_env(env: Mapping[str, str], *, instance: Instance | None = None) -> dict[str, str] | None:
    if not (platform := env_value(env, PLATFORM)):
        return None
    values = dict(env)
    if not (env_value(values, "SWB_PUBLIC_URL") or values.get("PUBLIC_URL")):
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
            raise ConfigError("Set SWB_PUBLIC_URL for this deployment.")
        values["SWB_PUBLIC_URL"] = url
    if not env_value(values, OWNER_KEY):
        key = volume_owner_key(values, create=True, instance=instance)
        if key is None:
            raise ConfigError("The deployment requires an owner key.")
        values[OWNER_KEY] = key
    return values
