from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from switchboard import cli
from switchboard.companion import runtime, service
from switchboard.companion.bluetooth import last_check
from switchboard.companion.state import CompanionError, CompanionState, state_path

COMPANION_ID = "cmp_" + "1" * 24
DEVICE_KEY = "swbd_" + "K" * 43


@pytest.fixture
def paired(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("SWB_COMPANION_HOME", str(home / "companion settings"))
    state = CompanionState("https://swb.example.com", COMPANION_ID, DEVICE_KEY, (), str(home / "Drop"))
    path = state_path()
    state.save(path)
    return path, state


def test_service_definitions():
    argv = ["/home/me/my interpreter", "-m", "switchboard.companion.service", '/home/me/%n "with" $DOLLAR']
    unit = service.systemd_unit(argv, "/usr/bin:/home/me/%n").decode()
    assert 'ExecStart="/home/me/my interpreter" "-m" "switchboard.companion.service"' in unit
    assert r'"/home/me/%%n \"with\" $$DOLLAR"' in unit
    assert 'Environment="PATH=/usr/bin:/home/me/%%n"' in unit
    assert "Restart=on-failure" in unit and "WantedBy=default.target" in unit
    with pytest.raises(CompanionError, match="printable"):
        service.systemd_unit(["/bin/python", "line\nbreak"], "/usr/bin")

    home = Path("/Users/me")
    log = home / "Library/Logs/swb/companion.log"
    agent = plistlib.loads(service.launch_agent("one.swb.test", argv, "/usr/bin", log, home))
    assert agent["ProgramArguments"] == argv and agent["Label"] == "one.swb.test"
    assert agent["RunAtLoad"] is True and agent["KeepAlive"] == {"SuccessfulExit": False}
    assert agent["WorkingDirectory"] == str(home) and agent["LimitLoadToSessionType"] == "Aqua"
    assert agent["StandardOutPath"] == agent["StandardErrorPath"] == str(log)

    sid = "S-1-5-21-12345-1001"
    task = ET.fromstring(service.scheduled_task(sid, argv, home))
    ns = {"": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert task.findtext("Triggers/LogonTrigger/UserId", namespaces=ns) == sid
    assert task.findtext("Principals/Principal/LogonType", namespaces=ns) == "InteractiveToken"
    assert task.findtext("Settings/ExecutionTimeLimit", namespaces=ns) == "PT0S"
    assert task.findtext("Settings/RestartOnFailure/Count", namespaces=ns) == "999"
    assert task.findtext("Actions/Exec/Arguments", namespaces=ns) == subprocess.list2cmdline(argv[1:])


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_install_start_stop_uninstall_are_idempotent(paired, monkeypatch, tmp_path, platform, capsys):
    path, state = paired
    calls = []
    manager = {"loaded": False, "running": False}
    executable = tmp_path / "venv" / "python"
    executable.parent.mkdir()
    executable.touch()
    executable.with_name("pythonw.exe").touch()
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "executable", str(executable))

    def run(*args):
        calls.append(args)
        code, stdout = 0, ""
        program, *arg = args
        if program == "whoami.exe":
            stdout = '"test-user","S-1-5-21-12345-1001"\n'
        elif program == "powershell.exe":
            stdout = "Running" if manager["running"] else ("Ready" if manager["loaded"] else "")
        elif program == "schtasks.exe":
            if arg[0] == "/create":
                manager["loaded"] = True
            elif arg[0] == "/run":
                manager["running"] = True
            elif arg[0] == "/end":
                manager["running"] = False
            elif arg[0] == "/delete":
                manager["loaded"] = False
        elif program == "/bin/launchctl":
            if arg[0] == "print" and arg[1].count("/") == 2:
                code = int(not manager["loaded"])
                stdout = "state = running" if manager["running"] else ""
            elif arg[0] in ("bootstrap", "kickstart"):
                manager.update(loaded=True, running=True)
            elif arg[0] == "bootout":
                manager.update(loaded=False, running=False)
        elif program == "systemctl":
            if arg[1] in ("start", "enable"):
                manager.update(loaded=True, running=True)
            elif arg[1] == "stop":
                manager["running"] = False
            elif arg[1] == "disable":
                manager.update(loaded=False, running=False)
            elif arg[1] == "is-active":
                code = 0 if manager["running"] else 3
        return subprocess.CompletedProcess(args, code, stdout, "")

    monkeypatch.setattr(service, "_run", run)
    svc = service.Service(path)
    svc.uninstall()
    svc.install(state)
    installed = svc.file.stat()
    svc.install(state)
    assert svc.file.stat().st_mtime_ns == installed.st_mtime_ns
    assert sum("bootstrap" in call or "/create" in call or "daemon-reload" in call for call in calls) == 1
    assert DEVICE_KEY.encode() not in svc.file.read_bytes()
    svc.status()
    assert capsys.readouterr().out.endswith("Service installed\nService running\n")
    svc.stop()
    svc.stop()
    assert not manager["running"]
    svc.start(state)
    assert manager["running"]
    svc.uninstall()
    svc.uninstall()
    svc.status()
    assert capsys.readouterr().out.endswith("Service absent\nService stopped\n")
    assert not svc.file.exists() and not manager["running"] and not manager["loaded"]
    assert CompanionState.load(path) == state
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_runtime_status_and_single_runner(paired):
    path, state = paired
    assert runtime.status(path, state.companion_id) == (False, "Offline", None)
    assert not path.with_suffix(".lock").exists()
    with runtime.running(path, state.companion_id, False) as active:
        assert runtime.status(path, state.companion_id) == (True, "Connecting", "Disabled")
        active.connection("Connected")
        assert runtime.status(path, state.companion_id) == (True, "Connected", "Disabled")
        with pytest.raises(CompanionError, match="already running"), runtime.running(path, state.companion_id, False):
            pytest.fail("A second companion acquired the same lease")
    assert runtime.status(path, state.companion_id) == (False, "Offline", None)
    active.connection("Connected")
    assert runtime.status(path, state.companion_id) == (False, "Offline", None)


def test_runtime_write_failure_does_not_stop_the_runner(paired, monkeypatch):
    path, state = paired

    def sharing_violation(*_):
        raise PermissionError

    monkeypatch.setattr(Path, "replace", sharing_violation)
    with runtime.running(path, state.companion_id, False) as active:
        active.connection("Connected")
        assert runtime.status(path, state.companion_id) == (True, "Starting", None)
    assert runtime.status(path, state.companion_id) == (False, "Offline", None)


def test_bluetooth_denial_does_not_install_the_service(paired, monkeypatch):
    path, state = paired
    monkeypatch.setattr(sys, "platform", "darwin")
    calls = []

    def run(*args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    def prepare(config):
        assert config == path
        Path(config).with_suffix(".bluetooth").write_text("Denied", encoding="utf-8")
        raise CompanionError("Bluetooth denied")

    monkeypatch.setattr(service, "_run", run)
    monkeypatch.setattr(service, "prepare", prepare)
    svc = service.Service(path)
    with pytest.raises(CompanionError, match="Bluetooth denied"):
        svc.install(replace(state, enabled_caps=("muse",)))
    assert not svc.file.exists()
    assert calls == [("/bin/launchctl", "print", svc.domain)]
    assert last_check(path) == "Denied (last check)"


def test_service_install_requires_pairing_but_cleanup_does_not(paired, monkeypatch, capsys):
    path, _ = paired
    path.unlink()
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(SystemExit) as error:
        cli.main(["companion", "service", "install"])
    assert error.value.code == 1 and "Pair this device" in capsys.readouterr().err
    path.write_text("invalid settings", encoding="utf-8")
    cli.main(["companion", "service", "uninstall"])
    assert capsys.readouterr().out == "Companion service removed.\n"
