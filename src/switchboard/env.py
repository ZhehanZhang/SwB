from __future__ import annotations

import logging
from collections.abc import Mapping
from threading import Lock

_PREFIX = "SWB_"
_LEGACY_PREFIX = "SWITCHBOARD_"
_LOG = logging.getLogger("switchboard")
_notified: set[str] = set()
_notice_lock = Lock()


def canonical_env_name(name: str) -> str:
    if name.startswith(_LEGACY_PREFIX):
        return _PREFIX + name.removeprefix(_LEGACY_PREFIX)
    return name


def env_value(env: Mapping[str, str], name: str, default: str | None = None) -> str | None:
    name = canonical_env_name(name)
    if name in env or not name.startswith(_PREFIX):
        return env.get(name, default)
    legacy = _LEGACY_PREFIX + name.removeprefix(_PREFIX)
    if legacy not in env:
        return default
    if _LOG.isEnabledFor(logging.INFO):
        with _notice_lock:
            first = legacy not in _notified
            _notified.add(legacy)
        if first:
            _LOG.info("Use %s in place of %s.", name, legacy)
    return env[legacy]
