from __future__ import annotations

import hashlib
import logging
import os
import platform
import queue
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Thread

from .cli import CONFIG, CliError, _instance_config, _url, _write_file
from .config import DEFAULT_PORT, INSTANCE_DB, LOOPBACK_HOSTS, Instance, bind_address, config_path, instance_data_dir, load_config
from .deployment import OWNER_KEY
from .store import ensure_private_data_dir

_TUNNELS = {
    "cloudflare": ("cloudflared", ("tunnel", "--no-autoupdate", "--url"), "trycloudflare.com"),
    "tailscale": ("tailscale", ("funnel",), "ts.net"),
}


def _install_hint(provider: str) -> str:
    if provider == "tailscale":
        return "https://tailscale.com/download"
    system = platform.system()
    if system == "Darwin":
        return "brew install cloudflared"
    if system == "Windows":
        return "winget install --exact --id Cloudflare.cloudflared"
    arch = {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "arm", "i686": "386"}.get(platform.machine())
    if system == "Linux" and arch:
        return ('mkdir -p "$HOME/.local/bin" && curl -fL '
                f'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}'
                ' -o "$HOME/.local/bin/cloudflared" && chmod +x "$HOME/.local/bin/cloudflared"\n'
                'Add $HOME/.local/bin to PATH if needed.')
    return "https://developers.cloudflare.com/tunnel/downloads/"


@contextmanager
def _tunnel(provider: str, origin: str) -> Iterator[str]:
    binary, flags, domain = _TUNNELS[provider]
    if not (executable := shutil.which(binary)):
        raise CliError(f"{binary} was not found on PATH. Install it with:\n{_install_hint(provider)}")

    pattern = re.compile(r"(?:^|\s)(https://(?:[a-z0-9-]+\.)+" + re.escape(domain) + r")(?=\s|$)", re.I)
    ready: queue.SimpleQueue[str | None] = queue.SimpleQueue()
    child_env = {k: os.environ[k] for k in ("PATH", "HOME", "USERPROFILE", "SystemRoot", "TEMP", "TMP", "TMPDIR", "LOCALAPPDATA")
                 if k in os.environ}
    try:
        process = subprocess.Popen([executable, *flags, origin], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", env=child_env)
    except OSError:
        raise CliError(f"Could not start {binary}. Check that it is executable.") from None

    assert process.stdout is not None
    output = process.stdout

    def read_output() -> None:
        reported = False
        for line in output:
            if not reported and (match := pattern.search(line)):
                ready.put(match[1])
                reported = True
        if not reported:
            ready.put(None)

    reader = Thread(target=read_output, daemon=True)
    try:
        reader.start()
        try:
            public_url = ready.get(timeout=30)
        except queue.Empty:
            public_url = None
        if not public_url or process.poll() is not None:
            hint = (" Make sure Tailscale is signed in and Funnel is enabled."
                    if provider == "tailscale" else " Check your network and Cloudflare Quick Tunnel setup.")
            raise CliError(f"{binary} did not start a tunnel.{hint}")
        yield public_url
    finally:
        if provider == "tailscale" and os.name == "posix":
            process.send_signal(signal.SIGINT)
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if reader.ident is not None:
            reader.join()
        output.close()


def _interrupt(*_: object) -> None:
    raise KeyboardInterrupt


def _notice_home_data(data: Path, instance_data: Path) -> None:
    instance_id = hashlib.sha256(os.fsencode(instance_data)).hexdigest()
    marker = data / f".instance-data-notice-{instance_id}"
    try:
        marker.touch(mode=0o600, exist_ok=False)
    except FileExistsError:
        return
    logging.getLogger("switchboard").warning(
        "Using existing data in %s instead of %s. Set SWITCHBOARD_DATA_DIR to choose a location.", data, instance_data,
    )


def run(base: Path, dotenv: dict[str, str], tunnel: str | None, *, host: str | None, instance: bool,
        public_url: str | None = None) -> None:
    from .__main__ import main as serve

    env = {**dotenv, **os.environ}
    raw_port = env.get("PORT", DEFAULT_PORT)
    if not raw_port.isascii() or not raw_port.isdecimal() or len(raw_port) > 5 or not 1 <= (port := int(raw_port)) <= 65535:
        raise CliError("PORT must be a number from 1 to 65535.")
    host, origin = bind_address(env, port, host=host)
    selected = Instance(base, explicit=instance)
    if public_url is not None:
        public_url = _url(public_url, public=True)
    path = config_path(None, env, instance=selected)
    first_run = not (env.get("SWITCHBOARD_CONFIG") or os.path.lexists(base / CONFIG) or path is not None and path.is_file())
    if first_run:
        prior_data = instance_data_dir(env, selected)
        first_run = not any((data / name).is_file() for data in {prior_data, base / "data"}
                            for name in (INSTANCE_DB, "capy-bridge.db"))
    configured = None
    if first_run or host not in LOOPBACK_HOSTS:
        configured = load_config(env=env, instance=selected, tunnel_url=public_url).public_url
        if host not in LOOPBACK_HOSTS and not configured:
            raise CliError("Set SWITCHBOARD_PUBLIC_URL to bind SwB beyond loopback.")
    values = {
        **{k: value for k, value in dotenv.items() if k not in os.environ},
        "SWITCHBOARD_HOST": host,
        "PORT": str(port),
        "FORWARDED_ALLOW_IPS": "127.0.0.1,::1",
    }
    previous = {k: os.environ.get(k) for k in values}
    previous_sigterm = signal.signal(signal.SIGTERM, _interrupt)
    try:
        with ExitStack() as stack:
            listener = None
            tunnel_url = public_url
            if tunnel:
                try:
                    family = socket.AF_INET6 if ":" in host else socket.AF_INET
                    listener = stack.enter_context(socket.create_server((host, port), family=family))
                except OSError:
                    raise CliError(f"Could not bind {origin}. Choose another PORT.") from None
                tunnel_url = stack.enter_context(_tunnel(tunnel, origin))
            if first_run:
                env_path = base / ".env"
                key = env.get(OWNER_KEY) or secrets.token_hex(32)
                if "\n" in key or "\r" in key:
                    raise CliError(f"Use a single line for {OWNER_KEY}.")
                exists = os.path.lexists(env_path)
                text = env_path.read_text(encoding="utf-8").rstrip("\n") + "\n" if exists else ""
                if dotenv.get(OWNER_KEY) != key:
                    text += f"{OWNER_KEY}={shlex.quote(key)}\n"
                if (dotenv.get("PORT") or DEFAULT_PORT) != str(port):
                    text += f"PORT={port}\n"
                ensure_private_data_dir(base / "data")
                _write_file(env_path, text, force=exists, mode=0o600)
                local_url = f"http://localhost:{port}"
                _instance_config(base, configured or local_url, force=False,
                                 alias=origin if not configured and host in LOOPBACK_HOSTS and origin != local_url else None)
                values[OWNER_KEY] = key
                previous[OWNER_KEY] = os.environ.get(OWNER_KEY)
            os.environ.update(values)
            serve(["serve"], listener=listener, tunnel_url=tunnel_url,
                  instance=selected, announce=True, announce_login=first_run, on_home_fallback=_notice_home_data)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        for k, value in previous.items():
            if value is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = value
