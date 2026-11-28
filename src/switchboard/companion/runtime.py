from __future__ import annotations

import errno
import json
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from .state import CompanionError


class AlreadyRunning(CompanionError):
    pass


@contextmanager
def _lease(path: Path, *, create: bool) -> Iterator[bool]:
    info = path.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or (os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077)):
        raise CompanionError("Choose a private companion settings directory.")
    flags = (os.O_CREAT if create else 0) | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path.with_suffix(".lock"), flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise CompanionError("Check the companion lock file.")
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                raise
            yield False
        else:
            yield True
    finally:
        os.close(fd)


class Runtime:
    def __init__(self, path: Path, companion_id: str, bluetooth: str) -> None:
        self.path = path.with_suffix(".runtime")
        self.companion_id = companion_id
        self.bluetooth = bluetooth

    def connection(self, connection: str) -> None:
        with suppress(OSError):
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    json.dump({"id": self.companion_id, "connection": connection, "bluetooth": self.bluetooth}, stream)
                    stream.close()
                    temporary.replace(self.path)
                finally:
                    temporary.unlink(missing_ok=True)


@contextmanager
def running(path: Path, companion_id: str, muse: bool) -> Iterator[Runtime]:
    with _lease(path, create=True) as acquired:
        if not acquired:
            raise AlreadyRunning("This companion is already running.")
        runtime = Runtime(path, companion_id, "Unknown" if muse else "Disabled")
        try:
            runtime.connection("Connecting")
            yield runtime
        finally:
            with suppress(OSError):
                runtime.path.unlink(missing_ok=True)


def status(path: Path, companion_id: str) -> tuple[bool, str, str | None]:
    try:
        with _lease(path, create=False) as acquired:
            if acquired:
                return False, "Offline", None
        try:
            with path.with_suffix(".runtime").open(encoding="utf-8") as stream:
                data = json.loads(stream.read(4096))
            if (isinstance(data, dict) and data.get("id") == companion_id
                    and data.get("connection") in ("Connecting", "Connected", "Reconnecting")
                    and isinstance(data.get("bluetooth"), str)):
                return True, data["connection"], data["bluetooth"]
        except (OSError, ValueError, RecursionError):
            pass
    except FileNotFoundError:
        return False, "Offline", None
    return True, "Starting", None
