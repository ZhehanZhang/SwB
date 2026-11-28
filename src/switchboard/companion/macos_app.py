from __future__ import annotations

import ctypes
import hashlib
import json
import os
import plistlib
import re
import stat
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from xml.parsers.expat import ExpatError

from .. import __version__
from .state import CompanionError

APP_NAME = "SwB Companion.app"
BUNDLE_ID = "one.swb.companion"
_ASSET = "swb-companion-macos.zip"
_RELEASE = "https://github.com/ZhehanZhang/SwB/releases/download/"
_DOWNLOAD_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
_MAX_ARCHIVE = 32 * 1024 * 1024
_MAX_EXTRACTED = 64 * 1024 * 1024
_RENAME_SWAP = 0x00000002


def app_path() -> Path:
    return Path.home() / "Applications" / APP_NAME


def _manifest() -> tuple[str, str]:
    try:
        data = json.loads(Path(__file__).with_name("macos-helper.json").read_bytes())
        digest, team = data["sha256"], data["team_id"]
        if re.fullmatch(r"[a-f0-9]{64}", digest) and re.fullmatch(r"[A-Z0-9]{10}", team):
            return digest, team
    except (OSError, ValueError, TypeError, KeyError):
        pass
    raise CompanionError("Install the official SwB release to use the Mac Bluetooth helper.")


def _system(*argv: str) -> None:
    try:
        result = subprocess.run(argv, check=False, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
        if result.returncode == 0:
            return
    except (OSError, subprocess.SubprocessError):
        pass
    raise CompanionError("macOS could not verify the SwB Companion app.")


def _bundle_info(app: Path) -> dict:
    try:
        if not stat.S_ISDIR(app.lstat().st_mode):
            raise ValueError
        with (app / "Contents" / "Info.plist").open("rb") as stream:
            info = plistlib.loads(stream.read(65_536))
        if info["CFBundleIdentifier"] == BUNDLE_ID and info["CFBundleExecutable"] == "SwBCompanion":
            return info
    except (OSError, ValueError, KeyError, TypeError, ExpatError, plistlib.InvalidFileException):
        pass
    raise CompanionError("Choose a valid SwB Companion app.")


def verify_app(app: Path, team: str, *, current: bool = True, gatekeeper: bool = False) -> None:
    info = _bundle_info(app)
    if current and info.get("CFBundleShortVersionString") != __version__:
        raise CompanionError("Install the SwB Companion app for this version of SwB.")
    if not re.fullmatch(r"[A-Z0-9]{10}", team):
        raise CompanionError("Check the SwB Companion signing identity.")
    requirement = (
        f'anchor apple generic and identifier "{BUNDLE_ID}" '
        "and certificate 1[field.1.2.840.113635.100.6.2.6] exists "
        "and certificate leaf[field.1.2.840.113635.100.6.1.13] exists "
        f'and certificate leaf[subject.OU] = "{team}"'
    )
    _system("/usr/bin/codesign", "--verify", "--deep", "--strict", "-R", f"={requirement}", str(app))
    if gatekeeper:
        _system("/usr/sbin/spctl", "--assess", "--type", "execute", str(app))


class _ReleaseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != "https" or target.netloc not in _DOWNLOAD_HOSTS:
            raise CompanionError("The SwB Companion download moved to an unexpected address.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(archive: Path, digest: str) -> None:
    hasher = hashlib.sha256()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _ReleaseRedirects())
    try:
        with opener.open(f"{_RELEASE}v{__version__}/{_ASSET}", timeout=30) as source, archive.open("xb") as target:
            size = 0
            while chunk := source.read(64 * 1024):
                size += len(chunk)
                if size > _MAX_ARCHIVE:
                    raise CompanionError("The SwB Companion download is too large.")
                hasher.update(chunk)
                target.write(chunk)
    except (OSError, ValueError):
        raise CompanionError("SwB could not download the Mac Bluetooth helper.") from None
    if hasher.hexdigest() != digest:
        raise CompanionError("The SwB Companion download did not match this SwB release.")


def _extract(archive: Path, directory: Path) -> Path:
    try:
        with zipfile.ZipFile(archive) as package:
            seen = set()
            size = 0
            for member in package.infolist():
                name = PurePosixPath(member.filename)
                path = name.as_posix().casefold()
                kind = stat.S_IFMT(member.external_attr >> 16)
                size += member.file_size
                if (not name.parts or name.parts[0] not in (APP_NAME, "__MACOSX")
                        or ".." in name.parts or "\\" in member.filename or path in seen
                        or kind not in (0, stat.S_IFDIR, stat.S_IFREG)
                        or len(seen) >= 128 or size > _MAX_EXTRACTED):
                    raise ValueError
                seen.add(path)
    except (OSError, ValueError, zipfile.BadZipFile):
        raise CompanionError("The SwB Companion archive has an invalid layout.") from None
    _system("/usr/bin/ditto", "-x", "-k", str(archive), str(directory))
    return directory / APP_NAME


def installed_app() -> Path:
    _, team = _manifest()
    app = app_path()
    if not app.exists():
        raise CompanionError("Set up SwB Companion before using Mac Bluetooth.")
    verify_app(app, team)
    return app


def install_app() -> None:
    digest, team = _manifest()
    app = app_path()
    old = app.exists() or app.is_symlink()
    if old:
        verify_app(app, team, current=False, gatekeeper=True)
        if _bundle_info(app).get("CFBundleShortVersionString") == __version__:
            return
    app.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".swb-companion-", dir=app.parent) as work:
        directory = Path(work)
        archive = directory / _ASSET
        _download(archive, digest)
        candidate = _extract(archive, directory)
        verify_app(candidate, team, gatekeeper=True)
        if old:
            rename = ctypes.CDLL(None).renamex_np
            if rename(os.fsencode(candidate), os.fsencode(app), _RENAME_SWAP):
                raise CompanionError("SwB could not replace the installed Bluetooth helper.")
        else:
            candidate.rename(app)
