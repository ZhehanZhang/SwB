from __future__ import annotations

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

from capy_bridge.app import create_app
from capy_bridge.core import Bridge

PASSWORD = "correct horse battery staple"
CAPY_KEY = "capy_" + "x" * 43
STATIC_KEY = "s" * 32
PUBLIC = "https://capy.example.test"
CAPTAIN = "jam_CAPTAIN0000000000000000000"
PROJECT = "project_ZINF"
OTHER_PROJECT = "project_OTHER"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
CLAUDE_CB = "https://claude.ai/api/mcp/auth_callback"


def base_config(**over: Any) -> dict[str, Any]:
    cfg = {
        "public_url": PUBLIC,
        "auth": {"owner_emails": ["owner@example.test"]},
        "capy": {"captain_id": CAPTAIN},
        "delegate": {
            "default_target": "captain",
            "daily_cap": 3,
            "projects": {"infra": {"id": PROJECT, "repos": ["example-org/links", "example-org/status-service"]}},
        },
    }
    cfg.update(over)
    return cfg


class Clock:
    def __init__(self, t: float = 1_764_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeCapy:
    """A mocked Capy API: records requests and answers from in-memory threads."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, Any]] = []
        self.threads: dict[str, dict[str, Any]] = {
            CAPTAIN: self.thread(CAPTAIN, PROJECT, title="Captain"),
            "jam_OTHER": self.thread("jam_OTHER", OTHER_PROJECT, title="Someone else's"),
        }
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.backward_supported = True
        self.uploads: list[tuple[str, bytes]] = []

    @staticmethod
    def thread(tid: str, project: str, title: str = "t", status: str = "idle", **extra: Any) -> dict[str, Any]:
        return {"id": tid, "projectId": project, "title": title, "status": status, "needsYou": False, "usage": {"totalCredits": 1.5}, "updatedAt": "2026-11-25T10:00:00Z", **extra}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "upload.example":
            self.uploads.append((path, request.content))
            return httpx.Response(200)
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, path, body))
        assert request.headers["authorization"] == f"Bearer {CAPY_KEY}"
        p = path.removeprefix("/api/v1/")
        m = request.method
        if m == "POST" and p == "threads":
            tid = "jam_NEW" + hashlib.sha256(body["requestId"].encode()).hexdigest()[:8].upper()
            self.threads[tid] = self.thread(tid, body["projectId"], title=body.get("title") or "new", status="working")
            self.messages[tid] = [{"id": "m0", "source": "user", "text": body["message"], "createdAt": "x"}]
            return httpx.Response(200, json=self.threads[tid])
        if m == "POST" and p == "files":
            f = body["files"][0]
            return httpx.Response(200, json={"targets": [{"fileId": "file_" + f["name"], "url": f"https://upload.example/{f['name']}", "expiresAt": "x"}]})
        if (mm := re.fullmatch(r"threads/([^/]+)/message", p)) and m == "POST":
            if mm.group(1) not in self.threads:
                return httpx.Response(404, json={"_tag": "capy/ThreadNotFound"})
            self.messages.setdefault(mm.group(1), []).append({"id": "mx", "source": "user", "text": body["text"], "createdAt": "x"})
            return httpx.Response(200, json={"id": "01MSG" + "0" * 21, "deduped": False})
        if (mm := re.fullmatch(r"threads/([^/]+)/messages", p)) and m == "GET":
            items = self.messages.get(mm.group(1), [])
            q = request.url.params
            if "before" in q:
                if not self.backward_supported:
                    return httpx.Response(400, json={"_tag": "capy/InvalidRequest"})
                return httpx.Response(200, json={"items": items[-int(q.get("limit", 50)):], "cursor": None, "beforeCursor": None})
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
        if p == f"captains/{CAPTAIN}/digest":
            return httpx.Response(200, json={"path": "NOTES.md", "markdown": "# Captain notes\n- doing things", "updatedAt": "2026-11-25T10:45:00Z"})
        if p == f"captains/{CAPTAIN}/crew":
            return httpx.Response(200, json={"items": [
                self.thread("jam_CREW1", PROJECT, title="slackgate", status="working", captain={"jamId": CAPTAIN, "name": "Captain"}),
                self.thread("jam_CREW2", PROJECT, title="needs owner", status="idle", needsYou=True, ask={"key": "a", "kind": "conversation", "text": "Merge zhehanzhang/x#1", "causeKey": None}, captain={"jamId": CAPTAIN, "name": "Captain"}),
            ]})
        if p == "captains":
            return httpx.Response(200, json={"items": [{"id": CAPTAIN, "name": "Captain", "title": "Captain"}], "cursor": None})
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


@pytest.fixture
def make(tmp_path, capy, cimd):
    made: list[TestClient] = []

    def build(config: dict | None = None, env: dict | None = None, clock: Clock | None = None, access=None):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(config if config is not None else base_config()))
        e = {"CAPY_API_KEY": CAPY_KEY, "CAPY_BRIDGE_OWNER_PASSWORD": PASSWORD, "CAPY_BRIDGE_KEY_CAPY": STATIC_KEY} if env is None else env
        bridge = Bridge(
            path, tmp_path / "data", env=e, clock=clock or Clock(),
            capy_transport=httpx.MockTransport(capy.handler),
            oauth_http=httpx.AsyncClient(transport=httpx.MockTransport(cimd.handler)),
            access_verifier=access,
        )
        client = TestClient(create_app(bridge), base_url=PUBLIC, follow_redirects=False)
        client.__enter__()
        made.append(client)
        return bridge, client

    yield build
    for c in made:
        c.__exit__(None, None, None)


def rpc(client: TestClient, token: str | None, method: str, params: dict | None = None) -> httpx.Response:
    h = dict(HEADERS)
    if token:
        h["Authorization"] = f"Bearer {token}"
    return client.post("/mcp", headers=h, content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}))


def call(client: TestClient, token: str, name: str, args: dict | None = None) -> dict[str, Any]:
    r = rpc(client, token, "tools/call", {"name": name, "arguments": args or {}})
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


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def register(client: TestClient, redirect: str = CLAUDE_CB, **extra: Any) -> dict[str, Any]:
    r = client.post("/register", json={"redirect_uris": [redirect], "client_name": "Claude", **extra})
    assert r.status_code == 201, r.text
    return r.json()


def consent(client: TestClient, client_id: str, redirect: str = CLAUDE_CB, challenge: str | None = None, state: str = "st4te", headers: dict | None = None) -> tuple[httpx.Response, str | None]:
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "code_challenge": challenge or pkce()[1], "code_challenge_method": "S256", "state": state, "resource": PUBLIC + "/mcp", "scope": "capy-bridge"}
    page = client.get("/authorize", params=params, headers=headers or {})
    m = re.search(r'name="req" value="([^"]+)"', page.text)
    return page, (m.group(1).replace("&quot;", '"') if m else None)


def authorize(client: TestClient, client_id: str, redirect: str = CLAUDE_CB, password: str = PASSWORD, challenge: str | None = None, state: str = "st4te") -> httpx.Response:
    page, req = consent(client, client_id, redirect, challenge, state)
    assert page.status_code == 200, page.text
    return client.post("/authorize", data={"req": req, "password": password, "decision": "approve"})


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
