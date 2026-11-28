from __future__ import annotations

import hashlib
import json
import os
import plistlib
import select
import signal
import stat
import subprocess
import sys
import venv
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest

from switchboard.companion import macos_app
from switchboard.companion.state import CompanionError


@pytest.fixture
def native_app():
    app = os.environ.get("SWB_TEST_MAC_HELPER")
    if not app:
        pytest.skip("needs the built Mac app")
    return Path(app)


def test_native_bundle_metadata():
    source = Path(__file__).resolve().parents[1] / "native" / "companion-macos" / "Info.plist"
    info = plistlib.loads(source.read_bytes())
    assert info["CFBundleIdentifier"] == macos_app.BUNDLE_ID
    assert info["CFBundleExecutable"] == "SwBCompanion"
    assert info["SwBCompanionServiceVersion"] == "1"
    assert not any(key.startswith("NSBluetooth") for key in info)


@pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS linkage tools")
def test_native_launcher_links_only_libsystem(native_app):
    info = plistlib.loads((native_app / "Contents" / "Info.plist").read_bytes())
    assert not any(key.startswith("NSBluetooth") for key in info)
    executable = native_app / "Contents" / "MacOS" / info["CFBundleExecutable"]
    output = subprocess.check_output(["/usr/bin/otool", "-arch", "all", "-L", executable], text=True, timeout=10)
    assert {line.split()[0] for line in output.splitlines() if line.startswith("\t")} == {"/usr/lib/libSystem.B.dylib"}


def test_native_launcher_execs_fixed_module(native_app, tmp_path):
    launcher = native_app / "Contents" / "MacOS" / "SwBCompanion"
    settings = tmp_path / "settings & logs"
    settings.mkdir(mode=0o700)
    environment = tmp_path / "python env"
    venv.EnvBuilder(symlinks=True).create(environment)
    python = environment / "bin" / "python"
    env = {**os.environ, "PYTHONHOME": "/invalid", "PYTHONPATH": str(tmp_path), "PYTHONUNBUFFERED": "0"}
    env.pop("__PYVENV_LAUNCHER__", None)
    package = subprocess.check_output(
        [python, "-E", "-P", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        text=True, env=env, timeout=10,
    )
    module = Path(package.strip()) / "switchboard" / "companion" / "service.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        """import json, os, signal, sys
signal.signal(signal.SIGTERM, lambda *_: sys.exit(37))
print("service stderr", file=sys.stderr)
print(json.dumps([os.getpid(), sys.argv[1:], sys.flags.ignore_environment, sys.flags.safe_path]))
signal.pause()
""",
        encoding="utf-8",
    )
    (tmp_path / "switchboard").mkdir()
    (tmp_path / "switchboard" / "__init__.py").write_text("raise RuntimeError('unexpected import')", encoding="utf-8")
    with subprocess.Popen(
        [launcher, python, settings], cwd=tmp_path, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) as process:
        try:
            assert select.select([process.stdout], [], [], 10)[0], "service did not log before exit"
            assert json.loads(process.stdout.readline()) == [process.pid, [str(settings)], 1, True]
            process.send_signal(signal.SIGTERM)
            assert process.communicate(timeout=10) == ("", "service stderr\n")
            assert process.returncode == 37
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)

    settings.chmod(0o755)
    for argv in ([python, settings], [python, "settings"], ["python", settings], [python, settings, "extra"]):
        result = subprocess.run([launcher, *argv], cwd=tmp_path, timeout=10, capture_output=True)
        assert result.returncode == 2 and not result.stdout and result.stderr


def test_missing_manifest_skips_app_install(tmp_path, monkeypatch):
    monkeypatch.setattr(macos_app, "__file__", str(tmp_path / "macos_app.py"))
    monkeypatch.setattr(macos_app, "app_path", Mock(side_effect=AssertionError))
    assert macos_app.install_app() is None


@pytest.mark.parametrize("raw", [b"{", b"{}", b"null", b" " * 1025, b'{"sha256":"bad","team_id":"ABCDEFGHIJ"}'])
def test_corrupt_manifest_does_not_fall_back(tmp_path, monkeypatch, raw):
    monkeypatch.setattr(macos_app, "__file__", str(tmp_path / "macos_app.py"))
    (tmp_path / "macos-helper.json").write_bytes(raw)
    monkeypatch.setattr(macos_app, "app_path", Mock(side_effect=AssertionError))
    with pytest.raises(CompanionError, match="release manifest is invalid"):
        macos_app.install_app()


@pytest.mark.parametrize("installed", ["absent", "previous", "current"])
def test_install_app_uses_the_verified_launcher(tmp_path, monkeypatch, installed):
    app = tmp_path / "Applications" / macos_app.APP_NAME
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(macos_app, "__file__", str(tmp_path / "macos_app.py"))
    digest, team = "a" * 64, "ABCDEFGHIJ"
    (tmp_path / "macos-helper.json").write_text(
        json.dumps({"sha256": digest, "team_id": team}), encoding="utf-8",
    )

    def make_bundle(path, *, launcher=True):
        plist = path / "Contents" / "Info.plist"
        plist.parent.mkdir(parents=True)
        info = {
            "CFBundleIdentifier": macos_app.BUNDLE_ID,
            "CFBundleExecutable": "SwBCompanion",
            "CFBundleShortVersionString": macos_app.__version__,
        }
        if launcher:
            info["SwBCompanionServiceVersion"] = "1"
        plist.write_bytes(plistlib.dumps(info))
        return path

    if installed != "absent":
        make_bundle(app, launcher=installed == "current")
    system, download = Mock(), Mock()
    monkeypatch.setattr(macos_app, "_system", system)
    monkeypatch.setattr(macos_app, "_download", download)
    monkeypatch.setattr(macos_app, "_extract", lambda _, directory: make_bundle(directory / macos_app.APP_NAME))

    def swap(src, dst, flags):
        assert flags == macos_app._RENAME_SWAP
        candidate = Path(os.fsdecode(src))
        assert system.call_args.args == ("/usr/sbin/spctl", "--assess", "--type", "execute", str(candidate))
        app.rename(candidate.with_suffix(".old"))
        candidate.rename(Path(os.fsdecode(dst)))
        return 0

    monkeypatch.setattr(macos_app.ctypes, "CDLL", Mock(return_value=Mock(renamex_np=swap)))
    assert macos_app.install_app() == (app / "Contents/MacOS/SwBCompanion", installed != "current")
    assert macos_app._bundle_info(app)["SwBCompanionServiceVersion"] == "1"
    assert system.call_count == (4 if installed == "previous" else 2)
    if installed == "current":
        download.assert_not_called()
    else:
        assert download.call_args.args[1] == digest


def test_download_uses_pinned_version_and_hash(tmp_path, monkeypatch):
    data = b"archive"
    opener = Mock()
    opener.open.side_effect = lambda *_args, **_kwargs: BytesIO(data)
    monkeypatch.setattr(macos_app.urllib.request, "build_opener", lambda *_: opener)
    archive = tmp_path / "archive.zip"
    macos_app._download(archive, hashlib.sha256(data).hexdigest())
    assert archive.read_bytes() == data
    opener.open.assert_called_once_with(
        f"https://github.com/ZhehanZhang/SwB/releases/download/v{macos_app.__version__}/swb-companion-macos.zip",
        timeout=30,
    )
    with pytest.raises(CompanionError, match="did not match"):
        macos_app._download(tmp_path / "wrong.zip", "0" * 64)


@pytest.mark.parametrize("name", ["../bad", "/bad", "SwB Companion.app/../../bad", "SwB Companion.app/good\\bad"])
def test_download_rejects_unsafe_archive_paths(tmp_path, monkeypatch, name):
    archive = tmp_path / "test.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr(name, b"bad")
    monkeypatch.setattr(macos_app, "_system", Mock(side_effect=AssertionError))
    with pytest.raises(CompanionError, match="invalid layout"):
        macos_app._extract(archive, tmp_path)


def test_download_rejects_symlinks(tmp_path, monkeypatch):
    archive = tmp_path / "test.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        entry = zipfile.ZipInfo("SwB Companion.app/Contents/link")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        stream.writestr(entry, b"/tmp")
    monkeypatch.setattr(macos_app, "_system", Mock(side_effect=AssertionError))
    with pytest.raises(CompanionError, match="invalid layout"):
        macos_app._extract(archive, tmp_path)


def test_verify_pins_developer_id_and_uses_both_macos_checks(tmp_path, monkeypatch):
    app = tmp_path / macos_app.APP_NAME
    info = app / "Contents" / "Info.plist"
    info.parent.mkdir(parents=True)
    info.write_bytes(plistlib.dumps({
        "CFBundleIdentifier": macos_app.BUNDLE_ID,
        "CFBundleExecutable": "SwBCompanion",
        "CFBundleShortVersionString": macos_app.__version__,
        "SwBCompanionServiceVersion": "1",
    }))
    system = Mock()
    monkeypatch.setattr(macos_app, "_system", system)
    macos_app.verify_app(app, "ABCDEFGHIJ", gatekeeper=True)
    verify, assess = [call.args for call in system.call_args_list]
    assert verify[:5] == ("/usr/bin/codesign", "--verify", "--deep", "--strict", "-R")
    assert verify[5].startswith("=anchor apple generic")
    assert 'certificate leaf[subject.OU] = "ABCDEFGHIJ"' in verify[5]
    assert macos_app.BUNDLE_ID in verify[5]
    assert assess[:4] == ("/usr/sbin/spctl", "--assess", "--type", "execute")
