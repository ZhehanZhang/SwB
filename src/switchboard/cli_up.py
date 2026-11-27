from __future__ import annotations

import hashlib
import logging
import os
import platform
import queue
import re
import shutil
import signal
import socket
import subprocess
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Thread

from .cli import CliError
from .config import DEFAULT_PORT, Instance, bind_address

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


def run(base: Path, dotenv: dict[str, str], tunnel: str | None, *, host: str | None, instance: bool) -> None:
    from .__main__ import main as serve

    env = {**dotenv, **os.environ}
    raw_port = env.get("PORT", DEFAULT_PORT)
    if not raw_port.isascii() or not raw_port.isdecimal() or len(raw_port) > 5 or not 1 <= (port := int(raw_port)) <= 65535:
        raise CliError("PORT must be a number from 1 to 65535.")
    host, origin = bind_address(env, port, host=host)
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
            tunnel_url = None
            if tunnel:
                try:
                    family = socket.AF_INET6 if ":" in host else socket.AF_INET
                    listener = stack.enter_context(socket.create_server((host, port), family=family))
                except OSError:
                    raise CliError(f"Could not bind {origin}. Choose another PORT.") from None
                tunnel_url = stack.enter_context(_tunnel(tunnel, origin))
            os.environ.update(values)
            serve(["serve"], listener=listener, tunnel_url=tunnel_url,
                  instance=Instance(base, explicit=instance), announce=True, on_home_fallback=_notice_home_data)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        for k, value in previous.items():
            if value is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = value
