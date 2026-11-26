from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import yaml
from starlette.testclient import TestClient

from switchboard.app import create_app
from switchboard.auth import Principal
from switchboard.core import Switchboard

OWNER = {"cf-access-jwt-assertion": "good"}
CAPY_KEY = "capy_" + "x" * 43
STATIC_KEY = "s" * 32
CHANNEL_KEY = "c" * 32
SECOND_KEY = "d" * 32
SERVICE_KEY = "capy_service_" + "y" * 30
PUBLIC = "https://switchboard.example.test"
CAPTAIN = "jam_CAPTAIN0000000000000000000"
CAPTAIN2 = "jam_CAPTAIN2000000000000000000"
PROJECT = "project_BCN"
OTHER_PROJECT = "project_OTHER"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
CLAUDE_CB = "https://claude.ai/api/mcp/auth_callback"
TASKS = "io.modelcontextprotocol/tasks"


def last_auth(capy: "FakeCapy", method: str, path_end: str) -> str:
    return [a for m, p, a in capy.auths if m == method and p.endswith(path_end)][-1]


def base_config(**over: Any) -> dict[str, Any]:
    cfg = {
        "public_url": PUBLIC,
        "auth": {"owner_emails": ["owner@example.test"], "cloudflare_access": {"team_domain": "access-team.example.test", "aud": "aud1"}},
        "destinations": {"capy": {"daily_cap": 3, "default_route": {"captain": CAPTAIN}, "reconnect_base_s": 0.01}},
    }
    cfg.update(over)
    return cfg


def default_env() -> dict[str, str]:
    return {"CAPY_API_KEY": CAPY_KEY, "CAPY_API_KEY_SERVICE": SERVICE_KEY, "SWITCHBOARD_KEY_CAPY": STATIC_KEY, "SWITCHBOARD_KEY_CHANNEL": CHANNEL_KEY, "SWITCHBOARD_KEY_SECOND": SECOND_KEY}


class Clock:
    def __init__(self, t: float = 1_764_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def sse(event: str, data: dict[str, Any], id: str | None = None) -> bytes:
    return ((f"id: {id}\n" if id else "") + f"event: {event}\ndata: {json.dumps(data)}\n\n").encode()


class FakeCapy:
    """A mocked Capy API: records requests, answers from in-memory threads, and serves thread streams tests feed."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, Any]] = []
        self.threads: dict[str, dict[str, Any]] = {
            CAPTAIN: self.thread(CAPTAIN, PROJECT, title="Captain"),
            CAPTAIN2: self.thread(CAPTAIN2, OTHER_PROJECT, title="Orbit Captain"),
            "jam_OTHER": self.thread("jam_OTHER", OTHER_PROJECT, title="Someone else's"),
        }
        self.messages: dict[str, list[dict[str, Any]]] = {CAPTAIN: [{"id": "01A", "source": "assistant", "text": "earlier", "createdAt": "x"}]}
        self.backward_supported = True
        self.uploads: list[tuple[str, bytes]] = []
        self.queues: dict[str, asyncio.Queue] = {}
        self.connects: list[tuple[str, str | None]] = []
        self.stream_status: dict[str, int] = {}
        self.keys = {CAPY_KEY, SERVICE_KEY}
        self.refused_keys: set[str] = set()
        self.device_denied: set[str] = set()
        self.client_keys: dict[str, str] = {}
        self.hidden_from: dict[str, set[str]] = {}
        self.auths: list[tuple[str, str, str]] = []
        self.captains: list[dict[str, Any]] = [
            {"id": CAPTAIN, "code": "BCN-1", "name": "Captain", "title": "Captain", "projectId": PROJECT},
            {"id": CAPTAIN2, "code": "ORBIT-3", "name": "Orbit Captain", "title": "Orbit Captain", "projectId": OTHER_PROJECT},
        ]
        self.projects: list[dict[str, Any]] = [
            {"id": PROJECT, "name": "Beacon", "code": "BCN", "repos": [{"repoFullName": "Example-Org/Web-App", "baseBranch": "main", "mode": "primary"},
                                                                   {"repoFullName": "Example-Org/Status-Service", "baseBranch": "main", "mode": "lazy"}]},
            {"id": OTHER_PROJECT, "name": "Orbit", "code": "ORBIT", "repos": [{"repoFullName": "sample-team/orbit", "baseBranch": "main", "mode": "primary"}]},
        ]

    @staticmethod
    def thread(tid: str, project: str, title: str = "t", status: str = "idle", **extra: Any) -> dict[str, Any]:
        return {"id": tid, "projectId": project, "title": title, "status": status, "needsYou": False, "usage": {"totalCredits": 1.5}, "updatedAt": "2026-11-25T10:00:00Z", **extra}

    def q(self, tid: str) -> asyncio.Queue:
        return self.queues.setdefault(tid, asyncio.Queue())

    def emit(self, tid: str, event: str, data: dict[str, Any], id: str | None = None) -> None:
        self.q(tid).put_nowait(sse(event, data, id))

    def ack(self, tid: str, id: str = "01A5") -> None:
        """The thread picks up the last message switchboard sent to tid: it starts working, and the user entry carries its clientKey."""
        self.emit(tid, "status", {"threadId": tid, "status": "working"})
        self.emit(tid, "transcript", {"id": "u" + id, "source": "user", "text": "(task)", "clientKey": self.client_keys[tid]}, id=id)

    def drop(self, tid: str) -> None:
        self.q(tid).put_nowait(None)

    def streams(self, tid: str) -> list[str | None]:
        return [c for t, c in self.connects if t == tid]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "upload.example":
            self.uploads.append((path, request.content))
            return httpx.Response(200)
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, path, body))
        assert request.headers["authorization"] in {f"Bearer {k}" for k in self.keys}
        self.auths.append((request.method, path, request.headers["authorization"][7:]))
        if request.headers["authorization"][7:] in self.refused_keys:
            return httpx.Response(403, json={"_tag": "capy/Forbidden", "message": "no access to this thread"})
        key = request.headers["authorization"][7:]
        p = path.removeprefix("/api/v1/")
        m = request.method
        if (mm := re.fullmatch(r"threads/([^/]+)/stream", p)):
            tid = mm.group(1)
            self.connects.append((tid, request.headers.get("last-event-id")))
            if tid in self.stream_status:
                return httpx.Response(self.stream_status[tid], json={"_tag": "capy/Forbidden"})
            queue = self.q(tid)

            async def frames():
                yield sse("status", {"threadId": tid, "status": self.threads.get(tid, {}).get("status", "working")})
                while True:
                    item = await queue.get()
                    if item is None:
                        return
                    yield item

            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=frames())
        if re.fullmatch(r"threads/([^/]+)/messages/([^/]+)/send-now", p) and m == "POST":
            if key in self.device_denied:
                return httpx.Response(403, json={"_tag": "capy/DeviceAccessDenied", "deviceId": "dev_1", "deviceName": "Build Machine", "status": "none", "held": False})
            return httpx.Response(200, json={"outcome": "tooLate"})
        if m == "POST" and p == "threads":
            tid = "jam_NEW" + hashlib.sha256(body["requestId"].encode()).hexdigest()[:8].upper()
            self.threads[tid] = self.thread(tid, body["projectId"], title=body.get("title") or "new", status="working")
            self.messages[tid] = [{"id": "01M0", "source": "user", "text": body["message"], "createdAt": "x"}]
            return httpx.Response(200, json=self.threads[tid])
        if m == "POST" and p == "files":
            f = body["files"][0]
            return httpx.Response(200, json={"targets": [{"fileId": "file_" + f["name"], "url": f"https://upload.example/{f['name']}", "expiresAt": "x"}]})
        if (mm := re.fullmatch(r"threads/([^/]+)/message", p)) and m == "POST":
            if mm.group(1) not in self.threads:
                return httpx.Response(404, json={"_tag": "capy/ThreadNotFound"})
            self.messages.setdefault(mm.group(1), []).append({"id": "01AU", "source": "user", "text": body["text"], "createdAt": "x"})
            if body.get("clientKey"):
                self.client_keys[mm.group(1)] = body["clientKey"]
            return httpx.Response(200, json={"id": "01MSG" + "0" * 21, "deduped": False})
        if (mm := re.fullmatch(r"threads/([^/]+)/messages", p)) and m == "GET":
            items = self.messages.get(mm.group(1), [])
            q = request.url.params
            if "before" in q:
                if not self.backward_supported:
                    return httpx.Response(400, json={"_tag": "capy/InvalidRequest"})
                page = items[-int(q.get("limit", 50)):]
                return httpx.Response(200, json={"items": page, "cursor": None, "beforeCursor": page[0]["id"] if page else None})
            start = int(q["after"]) if q.get("after") else 0
            chunk = items[start:start + 2]
            nxt = str(start + 2) if start + 2 < len(items) else None
            return httpx.Response(200, json={"items": chunk, "cursor": nxt, "beforeCursor": None})
        if (mm := re.fullmatch(r"threads/([^/]+)/attachments/([^/]+)/download", p)):
            return httpx.Response(200, json={"url": f"https://files.example/{mm.group(2)}?sig=1", "expiresAt": "2026-11-25T11:00:00Z"})
        if (mm := re.fullmatch(r"threads/([^/]+)", p)) and m == "GET":
            t = self.threads.get(mm.group(1))
            return httpx.Response(200, json=t) if t else httpx.Response(404, json={"_tag": "capy/ThreadNotFound"})
        if p == "threads" and m == "GET":
            return httpx.Response(200, json={"items": [t for t in self.threads.values() if t["projectId"] == request.url.params["projectId"]], "cursor": None})
        if (mm := re.fullmatch(r"captains/([^/]+)/digest", p)):
            return httpx.Response(200, json={"path": "NOTES.md", "markdown": f"# Captain notes for {mm.group(1)}\n- doing things", "updatedAt": "2026-11-25T10:45:00Z"})
        if (mm := re.fullmatch(r"captains/([^/]+)/crew", p)):
            return httpx.Response(200, json={"items": [
                self.thread("jam_CREW1", PROJECT, title="Release check", status="working", captain={"jamId": CAPTAIN, "name": "Captain"}),
                self.thread("jam_CREW2", PROJECT, title="needs owner", status="idle", needsYou=True, ask={"key": "a", "kind": "conversation", "text": "Merge example-org/web-app#1", "causeKey": None}, captain={"jamId": CAPTAIN, "name": "Captain"}),
                self.thread("jam_CREW3", OTHER_PROJECT, title="other project work", status="working", captain={"jamId": CAPTAIN, "name": "Captain"}),
            ]})
        if p == "captains":
            return httpx.Response(200, json={"items": [c for c in self.captains if c["id"] not in self.hidden_from.get(key, set())], "cursor": None})
        if (mm := re.fullmatch(r"projects/([^/]+)", p)):
            return httpx.Response(200, json={"id": mm.group(1), "name": "Beacon", "code": "BCN"}) if mm.group(1) in (PROJECT, OTHER_PROJECT) else httpx.Response(404, json={"_tag": "capy/ProjectNotFound"})
        if p == "projects":
            return httpx.Response(200, json={"items": self.projects})
        return httpx.Response(404, json={"_tag": "test/Unhandled", "message": f"{m} {p}"})


class CIMDHost:
    def __init__(self) -> None:
        self.docs: dict[str, Any] = {}
        self.fetches = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.fetches += 1
        doc = self.docs.get(str(request.url))
        return httpx.Response(200, json=doc) if doc is not None else httpx.Response(404)


@pytest.fixture
def capy() -> FakeCapy:
    return FakeCapy()


@pytest.fixture
def cimd() -> CIMDHost:
    return CIMDHost()


async def access_verifier(assertion: str) -> str | None:
    """Stands in for Cloudflare's signature check: 'good' is the owner's Access JWT, 'other' someone else's."""
    return {"good": "owner@example.test", "other": "someone@example.test"}.get(assertion)


def new_core(tmp_path, capy, cimd=None, config=None, env=None, clock=None, access=access_verifier, data=None) -> Switchboard:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config if config is not None else base_config()))
    return Switchboard(
        path, data or tmp_path / "data", env=default_env() if env is None else env, clock=clock or Clock(),
        oauth_http=httpx.AsyncClient(transport=httpx.MockTransport((cimd or CIMDHost()).handler)),
        access_verifier=access,
        destination_options={"capy": {"transport": httpx.MockTransport(capy.handler)}},
    )


def beacon(core: Switchboard) -> None:
    core.projects.create(slug="beacon", name="Beacon", links={"capy_project": PROJECT, "repos": ["example-org/web-app", "example-org/status-service"]})


@pytest.fixture
def make(tmp_path, capy, cimd):
    made: list[TestClient] = []

    def build(config: dict | None = None, env: dict | None = None, clock: Clock | None = None, access=access_verifier):
        core = new_core(tmp_path, capy, cimd, config, env, clock, access)
        client = TestClient(create_app(core), base_url=PUBLIC, follow_redirects=False)
        client.__enter__()
        made.append(client)
        return core, client

    yield build
    for c in made:
        c.__exit__(None, None, None)


@pytest.fixture
async def amake(tmp_path, capy):
    """Async variant: the app, its lifespan and the fake Capy share the test's event loop, so tests can feed streams mid-call."""
    closers: list[Any] = []

    async def build(config: dict | None = None, env: dict | None = None, clock: Clock | None = None):
        core = new_core(tmp_path, capy, None, config, env, clock)
        app = create_app(core)
        started, stop = asyncio.Event(), asyncio.Event()

        async def lifespan() -> None:
            async with app.starlette.router.lifespan_context(app.starlette):
                started.set()
                await stop.wait()

        task = asyncio.create_task(lifespan())
        await started.wait()
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC, timeout=30)

        async def close() -> None:
            if not stop.is_set():
                await client.aclose()
                stop.set()
                await task

        core._test_close = close
        closers.append(close)
        return core, client

    yield build
    for close in reversed(closers):
        await close()


def principal(source: str = "claude") -> Principal:
    return Principal(source=source, client_id=f"test:{source}", kind="key")


async def eventually(pred, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


def envelope(tasks: bool = False) -> dict[str, Any]:
    return {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {"extensions": {TASKS: {}}} if tasks else {}}


def rpc_body(method: str, params: dict | None) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def rpc(client: TestClient, token: str | None, method: str, params: dict | None = None) -> httpx.Response:
    h = dict(HEADERS)
    if token:
        h["Authorization"] = f"Bearer {token}"
    return client.post("/mcp", headers=h, content=rpc_body(method, params))


async def arpc(client: httpx.AsyncClient, token: str, method: str, params: dict | None = None, modern: bool = False, tasks: bool = False) -> httpx.Response:
    h = {**HEADERS, "Authorization": f"Bearer {token}"}
    params = dict(params or {})
    if modern or tasks:
        params["_meta"] = envelope(tasks)
        h |= {"mcp-protocol-version": "2026-07-28", "mcp-method": method}
        if method == "tools/call":
            h["mcp-name"] = params["name"]
    return await client.post("/mcp", headers=h, content=rpc_body(method, params))


def call(client: TestClient, token: str, name: str, args: dict | None = None) -> dict[str, Any]:
    r = rpc(client, token, "tools/call", {"name": name, "arguments": args or {}})
    assert r.status_code == 200, r.text
    return r.json()["result"]


async def acall(client: httpx.AsyncClient, token: str, name: str, args: dict | None = None, **kw: Any) -> dict[str, Any]:
    r = await arpc(client, token, "tools/call", {"name": name, "arguments": args or {}}, **kw)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def payload(result: dict[str, Any]) -> Any:
    sc = result.get("structuredContent")
    if sc is not None:
        return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc
    text = result["content"][0]["text"]
    try:
        return json.loads(text)
    except ValueError:
        return text


def error_text(result: dict[str, Any]) -> str:
    assert result.get("isError"), result
    return result["content"][0]["text"]


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def register(client: TestClient, redirect: str = CLAUDE_CB, **extra: Any) -> dict[str, Any]:
    r = client.post("/register", json={"redirect_uris": [redirect], "client_name": "Claude", **extra})
    assert r.status_code == 201, r.text
    return r.json()


def consent(client: TestClient, client_id: str, redirect: str = CLAUDE_CB, challenge: str | None = None, state: str = "st4te", headers: dict | None = None) -> tuple[httpx.Response, str | None]:
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "code_challenge": challenge or pkce()[1], "code_challenge_method": "S256", "state": state, "resource": PUBLIC + "/mcp", "scope": "switchboard"}
    page = client.get("/authorize", params=params, headers=OWNER if headers is None else headers)
    m = re.search(r'name="req" value="([^"]+)"', page.text)
    return page, (m.group(1).replace("&quot;", '"') if m else None)


def authorize(client: TestClient, client_id: str, redirect: str = CLAUDE_CB, challenge: str | None = None, state: str = "st4te") -> httpx.Response:
    page, req = consent(client, client_id, redirect, challenge, state)
    assert page.status_code == 200, page.text
    return client.post("/authorize", data={"req": req, "decision": "approve"}, headers=OWNER)


def sign_in(client: TestClient, redirect: str = CLAUDE_CB, client_id: str | None = None) -> dict[str, Any]:
    cid = client_id or register(client, redirect)["client_id"]
    verifier, challenge = pkce()
    r = authorize(client, cid, redirect, challenge=challenge)
    assert r.status_code == 302, r.text
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["iss"] == [PUBLIC] and q["state"] == ["st4te"]
    tok = client.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": redirect, "client_id": cid, "code_verifier": verifier, "resource": PUBLIC + "/mcp"})
    assert tok.status_code == 200, tok.text
    return {**tok.json(), "client_id": cid}
