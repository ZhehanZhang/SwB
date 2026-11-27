import os
import socket
import subprocess
import sys
import time
from http.cookies import SimpleCookie
from pathlib import Path
from queue import Queue
from threading import Thread
from urllib.parse import urlsplit
from zipfile import ZipFile

import httpx

from switchboard.sessions import SESSION_COOKIE


with ZipFile(sys.argv[1]) as wheel:
    index = wheel.read("switchboard/static/app/index.html")

with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]

env = {**os.environ, "PORT": str(port)}
swb = Path(sys.executable).with_name("swb")
endpoint = f"http://127.0.0.1:{port}"
origin = f"http://localhost:{port}"

with subprocess.Popen([swb, "up"], env=env, stdout=subprocess.PIPE, text=True) as server:
    lines: Queue[str] = Queue()

    def read_stdout() -> None:
        for line in server.stdout:
            lines.put(line.strip())

    reader = Thread(target=read_stdout, daemon=True)
    reader.start()
    try:
        with httpx.Client(trust_env=False, timeout=2) as client:
            deadline = time.monotonic() + 15
            while True:
                if server.poll() is not None or time.monotonic() >= deadline:
                    raise SystemExit("SwB failed to start from the installed wheel.")
                try:
                    health = client.get(f"{endpoint}/healthz")
                except httpx.RequestError:
                    time.sleep(0.1)
                    continue
                assert health.is_success and health.json()["status"] == "ok"
                break

            assert lines.get(timeout=3) == f"MCP URL: {origin}/mcp"
            link = lines.get(timeout=3)
            url = urlsplit(link)
            assert f"{url.scheme}://{url.netloc}{url.path}" == f"{origin}/login"
            name, _, token = url.fragment.partition("=")
            assert name == "t" and len(token) == 43
            login_page = client.get(f"{origin}/login")
            assert login_page.status_code == 200 and login_page.content == index
            session = client.post(f"{origin}/auth/link", json={"token": token}, headers={"Origin": origin})
            assert session.status_code == 204
            followup = subprocess.run([swb, "login-link"], text=True,
                                      capture_output=True, timeout=15, check=True).stdout.strip()
            assert followup.startswith(f"{origin}/login#t=") and followup != link
            assert not (Path.home() / ".local/share/switchboard/switchboard.db").exists()
            cookie = SimpleCookie(session.headers["Set-Cookie"])[SESSION_COOKIE]
            assert cookie["secure"]
            headers = {"Cookie": f"{SESSION_COOKIE}={cookie.value}"}
            for path in ("/", "/agents", "/tokens"):
                response = client.get(f"{origin}{path}", headers=headers)
                assert response.status_code == 200 and response.content == index, path
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
        reader.join()

print("Installed wheel console sign-in and routes passed.")
