from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

from .caps.base import CapabilityError
from .state import CompanionError

_PERMISSIONS = {"Allowed", "Denied", "Restricted", "Approval required", "Unavailable"}


def last_check(path: Path) -> str:
    try:
        status = path.with_suffix(".bluetooth").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return "Unknown"
    return f"{status} (last check)" if status in _PERMISSIONS else "Unknown"


def _remember(path: Path, status: str) -> None:
    with suppress(OSError):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(status)
                stream.close()
                os.replace(temporary, path.with_suffix(".bluetooth"))
            finally:
                temporary.unlink(missing_ok=True)


async def current(path: Path) -> str:
    if sys.platform == "darwin":
        from .macos_transport import permission

        try:
            result = await permission()
        except (CompanionError, CapabilityError, OSError, TimeoutError):
            return "Unavailable"
        _remember(path, result)
        return result
    if sys.platform == "linux":
        from .caps.muse import _linux_adapter

        return "Managed by BlueZ" if _linux_adapter() else "Adapter unavailable"
    return "Unsupported"


def prepare(path: Path) -> None:
    from .macos_app import install_app
    from .macos_transport import permission

    install_app()

    async def request() -> str:
        result = await permission()
        if result == "Approval required":
            print("Allow Bluetooth access for SwB Companion when macOS asks.", flush=True)
            result = await permission(authorize=True)
        _remember(path, result)
        return result

    try:
        result = asyncio.run(request())
    except (CapabilityError, OSError, TimeoutError):
        raise CompanionError("SwB Companion could not check access to Bluetooth.") from None
    if result != "Allowed":
        raise CompanionError(
            f"Bluetooth {result.lower()}. Allow SwB Companion in System Settings → Privacy & Security → Bluetooth."
        )
