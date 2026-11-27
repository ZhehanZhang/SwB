from __future__ import annotations

import asyncio
import base64
import os
import re
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .base import ActionSpec, Capability, CapabilityError, JobContext

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_ENCODED_SIZE = 4 * ((MAX_FILE_SIZE + 2) // 3)
_NAME = {"type": "string", "minLength": 1, "maxLength": 255}


def _valid_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 255
        and ".." not in value
        and not value.endswith((" ", "."))
        and not re.search(r'[\x00-\x1f/\\:<>|?*"\ud800-\udfff]', value)
        and not re.fullmatch(r"CON|CONIN\$|CONOUT\$|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]",
                             value.split(".")[0].rstrip(), re.I)
        and not value.startswith(".swb-drop-")
    )


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _is_file(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and not _is_reparse(info) and info.st_nlink == 1


class _Folder:
    def __init__(self, path: Path, fd: int | None, info: os.stat_result):
        self.path = path
        self.fd = fd
        self.info = info

    def check(self) -> None:
        if self.fd is None:
            current = self.path.lstat()
            if _is_reparse(current) or not os.path.samestat(self.info, current):
                raise CapabilityError("Use a regular Drop folder.")

    def entry(self, name: str) -> str | Path:
        return name if self.fd is not None else self.path / name

    def stat(self, name: str) -> os.stat_result:
        self.check()
        return os.stat(self.entry(name), dir_fd=self.fd, follow_symlinks=False)

    def list(self) -> list[dict[str, Any]]:
        self.check()
        files = []
        for name in sorted(os.listdir(self.fd if self.fd is not None else self.path)):
            if not _valid_name(name):
                continue
            try:
                info = self.stat(name)
            except FileNotFoundError:
                continue
            if _is_file(info):
                files.append({"name": name, "size": info.st_size})
        return files

    def get(self, name: str) -> bytes:
        info = self.stat(name)
        if not _is_file(info):
            raise CapabilityError("Choose a regular file in the Drop folder.")
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                 | getattr(os, "O_BINARY", 0))
        with os.fdopen(os.open(self.entry(name), flags, dir_fd=self.fd), "rb") as stream:
            self.check()
            opened = os.fstat(stream.fileno())
            if not _is_file(opened) or not os.path.samestat(info, opened):
                raise CapabilityError("Choose a regular file in the Drop folder.")
            if opened.st_size > MAX_FILE_SIZE or len(data := stream.read(MAX_FILE_SIZE + 1)) > MAX_FILE_SIZE:
                raise CapabilityError("Use a file up to 10 MiB.")
            return data

    def put(self, name: str, data: bytes) -> str:
        temporary = self.entry(".swb-drop-" + secrets.token_hex(16))
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        self.check()
        fd = os.open(temporary, flags, 0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            stem, extension = os.path.splitext(name)
            for index in range(1, 10_001):
                output = name if index == 1 else f"{stem} ({index}){extension}"
                self.check()
                try:
                    if self.fd is None:
                        os.rename(temporary, self.entry(output))
                    else:
                        os.link(temporary, self.entry(output), src_dir_fd=self.fd, dst_dir_fd=self.fd)
                except FileExistsError:
                    continue
                return output
            raise CapabilityError("Choose a different file name.")
        finally:
            self.check()
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass


class DropCapability(Capability):
    id = "drop"
    version = "1"
    title = "Drop folder"
    actions = {
        "list": ActionSpec("List files in the Drop folder.", {
            "type": "object", "properties": {}, "additionalProperties": False,
        }),
        "get": ActionSpec("Read a file from the Drop folder.", {
            "type": "object",
            "properties": {"name": _NAME},
            "required": ["name"],
            "additionalProperties": False,
        }),
        "put": ActionSpec("Save a file in the Drop folder.", {
            "type": "object",
            "properties": {"name": _NAME, "content_b64": {"type": "string", "maxLength": MAX_ENCODED_SIZE}},
            "required": ["name", "content_b64"],
            "additionalProperties": False,
        }),
    }

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def _folder(self):
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.path.lstat()
        if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
            raise CapabilityError("Use a regular Drop folder.")
        if os.name == "nt":
            yield _Folder(self.path, None, info)
            return
        fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(fd)
            if not os.path.samestat(info, opened):
                raise CapabilityError("Use a regular Drop folder.")
            yield _Folder(self.path, fd, opened)
        finally:
            os.close(fd)

    def available(self) -> str | None:
        try:
            info = self.path.lstat()
            if stat.S_ISDIR(info.st_mode) and not _is_reparse(info):
                return None
        except FileNotFoundError:
            return None
        except OSError:
            pass
        return "Use a regular Drop folder."

    async def invoke(self, action: str, args: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
        return await asyncio.to_thread(self._invoke, action, args)

    def _invoke(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        if action not in self.actions:
            raise CapabilityError("Choose a Drop action.")
        properties = self.actions[action].schema["properties"]
        if set(args) != properties.keys():
            raise CapabilityError("Check the Drop action arguments.")
        name = args.get("name")
        if action != "list" and not _valid_name(name):
            raise CapabilityError("Choose a file name in the Drop folder.")
        if action == "put":
            encoded = args["content_b64"]
            if not isinstance(encoded, str):
                raise CapabilityError("Use base64 file content.")
            if len(encoded) > MAX_ENCODED_SIZE:
                raise CapabilityError("Use a file up to 10 MiB.")
            try:
                data = base64.b64decode(encoded, validate=True)
            except ValueError:
                raise CapabilityError("Use base64 file content.") from None
            if len(data) > MAX_FILE_SIZE:
                raise CapabilityError("Use a file up to 10 MiB.")
        try:
            with self._folder() as folder:
                if action == "list":
                    return {"files": folder.list()}
                if action == "get":
                    return {"name": name, "content_b64": base64.b64encode(folder.get(name)).decode("ascii")}
                return {"name": folder.put(name, data)}
        except OSError:
            raise CapabilityError("Check the Drop folder and file permissions.") from None
