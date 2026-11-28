from __future__ import annotations

import argparse
import hashlib
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from xml.etree import ElementTree as ET

from . import macos_app, runtime
from .state import CompanionError, CompanionState, state_path


def _path() -> str:
    return os.pathsep.join(p for p in os.get_exec_path() if Path(p).is_absolute()) or os.defpath


def launch_agent(label: str, argv: list[str], path: str, log: Path, home: Path) -> bytes:
    return plistlib.dumps({
        "Label": label,
        "ProgramArguments": argv,
        "EnvironmentVariables": {"PATH": path},
        "WorkingDirectory": str(home),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "LimitLoadToSessionType": "Aqua",
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "Umask": 0o077,
    })


def _systemd_quote(value: str, *, command: bool = False) -> str:
    if not value.isprintable() and value:
        raise CompanionError("Use printable paths for the companion service.")
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if command:
        value = value.replace("$", "$$")
    return f'"{value}"'


def systemd_unit(argv: list[str], path: str) -> bytes:
    command = " ".join(_systemd_quote(arg, command=True) for arg in argv)
    return f"""[Unit]
Description=SwB companion

[Service]
Type=exec
ExecStart={command}
Environment={_systemd_quote(f'PATH={path}')}
WorkingDirectory=%h
UMask=0077
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
""".encode()


def scheduled_task(sid: str, argv: list[str], home: Path) -> bytes:
    task = ET.Element("Task", version="1.2", xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task")
    trigger = ET.SubElement(ET.SubElement(task, "Triggers"), "LogonTrigger")
    ET.SubElement(trigger, "UserId").text = sid
    principal = ET.SubElement(ET.SubElement(task, "Principals"), "Principal", id="Companion")
    ET.SubElement(principal, "UserId").text = sid
    ET.SubElement(principal, "LogonType").text = "InteractiveToken"
    ET.SubElement(principal, "RunLevel").text = "LeastPrivilege"
    settings = ET.SubElement(task, "Settings")
    for key, value in {
        "MultipleInstancesPolicy": "IgnoreNew",
        "DisallowStartIfOnBatteries": "false",
        "StopIfGoingOnBatteries": "false",
        "StartWhenAvailable": "true",
        "ExecutionTimeLimit": "PT0S",
    }.items():
        ET.SubElement(settings, key).text = value
    restart = ET.SubElement(settings, "RestartOnFailure")
    ET.SubElement(restart, "Interval").text = "PT1M"
    ET.SubElement(restart, "Count").text = "999"
    action = ET.SubElement(ET.SubElement(task, "Actions", Context="Companion"), "Exec")
    ET.SubElement(action, "Command").text = argv[0]
    ET.SubElement(action, "Arguments").text = subprocess.list2cmdline(argv[1:])
    ET.SubElement(action, "WorkingDirectory").text = str(home)
    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


def _run(*argv: str) -> subprocess.CompletedProcess[str]:
    try:
        if os.name == "nt":
            system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
            if argv[0] == "powershell.exe":
                system /= "WindowsPowerShell/v1.0"
            argv = (str(system / argv[0]), *argv[1:])
        elif not Path(argv[0]).is_absolute():
            executable = shutil.which(argv[0], path=_path())
            if executable is None:
                raise OSError
            argv = (executable, *argv[1:])
        return subprocess.run(
            argv, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise CompanionError("The companion service manager is unavailable.") from None


def _check(result: subprocess.CompletedProcess[str], message: str) -> None:
    if result.returncode:
        raise CompanionError(message)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _private_log(path: Path) -> int:
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags, 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or
            (os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1))):
        os.close(fd)
        raise CompanionError("Choose a private companion log file.")
    return fd


class Service:
    def __init__(self, config: Path) -> None:
        self.config = Path(os.path.abspath(config))
        default = Path(os.path.abspath(state_path(use_override=False)))
        name = "swb-companion"
        if self.config != default:
            name += "-" + hashlib.sha256(os.fsencode(self.config.parent)).hexdigest()[:16]
        self.home = Path.home()
        self.platform = sys.platform
        if self.platform == "darwin":
            self.name = f"one.swb.{name}"
            self.domain = f"gui/{os.getuid()}"
            self.target = f"{self.domain}/{self.name}"
            self.file = self.home / "Library" / "LaunchAgents" / f"{self.name}.plist"
            self.log = self.home / "Library" / "Logs" / "swb" / f"{name}.log"
        elif self.platform == "linux":
            self.name = f"{name}.service"
            config_home = Path(os.environ.get("XDG_CONFIG_HOME", ""))
            if not config_home.is_absolute():
                config_home = self.home / ".config"
            self.file = config_home / "systemd" / "user" / self.name
        elif self.platform == "win32":
            identity = _run("whoami.exe", "/user", "/fo", "csv", "/nh")
            match = re.search(r'"(S-1-(?:[0-9]+-)*[0-9]+)"', identity.stdout)
            if identity.returncode or match is None:
                raise CompanionError("Windows could not read your user account.")
            self.sid = match[1]
            self.name = f"{name}-{self.sid}"
            self.file = self.config.parent / "companion-task.xml"
            self.log = self.config.parent / "companion.log"
        else:
            raise CompanionError("Companion services support macOS, Linux and Windows.")

    def _python(self) -> Path:
        executable = Path(sys.executable)
        prefix = Path(sys.prefix)
        if (not executable.is_absolute() or "archive-v0" in prefix.parts
                or (".cache" in prefix.parts and "pipx" in prefix.parts)):
            raise CompanionError("Install a persistent swb command with uv tool install swb-one first.")
        if self.platform == "win32":
            executable = executable.with_name("pythonw.exe")
        if not executable.is_file():
            raise CompanionError("Install a persistent Python environment for the companion.")
        return executable

    def _definition(self, executable: Path, helper: Path | None) -> bytes:
        config = str(self.config.parent)
        argv = [str(executable), "-E", "-P", "-u", "-m", "switchboard.companion.service", config]
        if self.platform == "darwin":
            if helper is not None:
                argv = [str(helper), str(executable), config]
            return launch_agent(self.name, argv, _path(), self.log, self.home)
        if self.platform == "linux":
            return systemd_unit(argv, _path())
        return scheduled_task(self.sid, [*argv, str(self.log)], self.home)

    def _windows_state(self) -> str:
        result = _run(
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "$ErrorActionPreference = 'Stop';"
            "$task = Get-ScheduledTask -TaskPath '\\' "
            f"| Where-Object TaskName -CEQ '{self.name}';"
            " if ($task) { $task.State.ToString() }",
        )
        _check(result, "Windows could not read the companion task.")
        return result.stdout.strip()

    def _launchd_state(self) -> str:
        result = _run("/bin/launchctl", "print", self.target)
        return result.stdout if result.returncode == 0 else ""

    def _linux_ready(self) -> None:
        _check(_run("systemctl", "--user", "show", "--property=Version", "--value"),
               "Start a systemd user session to manage the companion service.")

    def _running(self) -> bool:
        if self.platform == "darwin":
            return bool(re.search(r"(?m)^\s*state = running$", self._launchd_state()))
        if self.platform == "linux":
            return _run("systemctl", "--user", "is-active", "--quiet", self.name).returncode == 0
        return self._windows_state() == "Running"

    def status(self) -> None:
        installed = bool(self._windows_state()) if self.platform == "win32" else self.file.is_file()
        print("Service installed" if installed else "Service absent")
        print("Service running" if installed and self._running() else "Service stopped")

    def _require_available(self, state: CompanionState) -> None:
        if runtime.status(self.config, state.companion_id)[0] and not self._running():
            raise CompanionError("Stop the foreground companion before starting the service.")

    def install(self, state: CompanionState) -> None:
        self._require_available(state)
        executable = self._python()
        helper = None
        app_changed = False
        if self.platform == "linux":
            self._linux_ready()
        elif self.platform == "darwin":
            _check(_run("/bin/launchctl", "print", self.domain), "Sign in to macOS to install the companion service.")
            self.log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.close(_private_log(self.log))
            if installed := macos_app.install_app():
                helper, app_changed = installed

        definition = self._definition(executable, helper)
        previous = self.file.read_bytes() if self.file.is_file() else None
        changed = previous != definition
        if previous is not None and (changed or app_changed):
            self.stop()
        if changed:
            _write(self.file, definition)
        if self.platform == "linux":
            if changed:
                _check(_run("systemctl", "--user", "daemon-reload"), "systemd could not load the companion unit.")
            _check(_run("systemctl", "--user", "enable", "--now", self.name), "systemd could not enable the companion service.")
        else:
            if self.platform == "win32" and (changed or not self._windows_state()):
                _check(_run("schtasks.exe", "/create", "/tn", self.name, "/xml", str(self.file), "/f"),
                       "Windows could not install the companion task.")
            self.start(state)
        print("Companion service installed.")

    def start(self, state: CompanionState) -> None:
        if not self.file.is_file():
            raise CompanionError("Run swb companion service install first.")
        self._require_available(state)
        if self.platform == "darwin":
            loaded = self._launchd_state()
            if not loaded:
                result = _run("/bin/launchctl", "bootstrap", self.domain, str(self.file))
            elif not re.search(r"(?m)^\s*state = running$", loaded):
                result = _run("/bin/launchctl", "kickstart", self.target)
            else:
                return
            _check(result, "launchd could not start the companion service.")
        elif self.platform == "linux":
            self._linux_ready()
            _check(_run("systemctl", "--user", "start", self.name), "systemd could not start the companion service.")
        elif not self._running():
            _check(_run("schtasks.exe", "/run", "/tn", self.name), "Windows could not start the companion task.")

    def stop(self) -> None:
        if self.platform == "darwin" and self._launchd_state():
            _check(_run("/bin/launchctl", "bootout", self.target), "launchd could not stop the companion service.")
        elif self.platform == "linux" and self.file.is_file():
            self._linux_ready()
            _check(_run("systemctl", "--user", "stop", self.name), "systemd could not stop the companion service.")
        elif self.platform == "win32" and self._running():
            result = _run("schtasks.exe", "/end", "/tn", self.name)
            if result.returncode and self._running():
                _check(result, "Windows could not stop the companion task.")

    def uninstall(self) -> None:
        if self.platform == "linux" and self.file.is_file():
            self._linux_ready()
            _check(_run("systemctl", "--user", "disable", "--now", self.name), "systemd could not disable the companion service.")
            self.file.unlink()
            _check(_run("systemctl", "--user", "daemon-reload"), "systemd could not reload the user units.")
        elif self.platform != "linux":
            self.stop()
            if self.platform == "win32" and self._windows_state():
                _check(_run("schtasks.exe", "/delete", "/tn", self.name, "/f"), "Windows could not remove the companion task.")
            self.file.unlink(missing_ok=True)
        print("Companion service removed.")

    def logs(self) -> None:
        if self.platform == "linux":
            result = _run("journalctl", "--user", "--unit", self.name, "--no-pager", "--lines", "100", "--output", "cat")
            _check(result, "The user journal is unavailable.")
            output = result.stdout
        else:
            try:
                with self.log.open("rb") as stream:
                    size = os.fstat(stream.fileno()).st_size
                    stream.seek(max(0, size - 65_536))
                    output = stream.read(65_536).decode("utf-8", "replace")
            except FileNotFoundError:
                output = ""
        print(output.rstrip() or "The companion service log is empty.")


def command(action: str, path: Path, state: CompanionState | None) -> None:
    service = Service(path)
    if action in ("install", "start"):
        if state is None:
            raise CompanionError("Pair this device with swb companion connect first.")
        if action == "install":
            service.install(state)
        else:
            service.start(state)
            print("Companion service started.")
    elif action == "stop":
        service.stop()
        print("Companion service stopped.")
    elif action == "uninstall":
        service.uninstall()
    elif action == "logs":
        service.logs()
    else:
        service.status()


def main() -> None:
    from .commands import run

    os.environ["SWB_COMPANION_HOME"] = sys.argv[1]
    def start() -> None:
        try:
            run(argparse.Namespace(companion_action="run", background=True), lambda _: "")
        except (CompanionError, OSError) as error:
            print(f"SwB companion: {error}", file=sys.stderr, flush=True)
            raise SystemExit(1) from None
        except Exception:
            print("The companion service failed.", file=sys.stderr, flush=True)
            raise SystemExit(1) from None

    if len(sys.argv) > 2:
        with os.fdopen(_private_log(Path(sys.argv[2])), "a", encoding="utf-8", buffering=1) as log:
            with redirect_stdout(log), redirect_stderr(log):
                start()
    else:
        start()


if __name__ == "__main__":
    main()
