import re
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import OWNER, PUBLIC, STATIC_KEY, base_config
from switchboard import web


@pytest.fixture
def web_dist(tmp_path, monkeypatch):
    dist = tmp_path / "web"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text('<!doctype html><script src="/app-assets/assets/app-hash.js"></script>')
    (assets / "app-hash.js").write_text("export {};")
    monkeypatch.setattr(web, "APP_DIST", dist)
    return dist


@pytest.mark.parametrize("path", ["/", "/canvas", "/agents", "/inbox", "/provision", "/projects", "/memory", "/audit", "/tokens", "/settings",
                                  "/agents/client/setup", "/inbox/thread/one", "/canvas/"])
def test_spa_paths_require_owner_and_serve_uncached_shell(make, web_dist, path):
    _, client = make()
    for headers in ({}, {"authorization": f"Bearer {STATIC_KEY}"}):
        signed_out = client.get(path, headers=headers)
        location = urlsplit(signed_out.headers["location"])
        assert signed_out.status_code == 303 and location.path == "/login"
        assert parse_qs(location.query) == {"next": [path]}
    response = client.get(path, headers=OWNER)
    assert response.status_code == 200
    assert response.text == (web_dist / "index.html").read_text()
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["referrer-policy"] == "same-origin"
    directives = dict(d.strip().split(" ", 1) for d in response.headers["content-security-policy"].split(";"))
    assert directives["script-src"] == directives["connect-src"] == directives["font-src"] == "'self'"
    assert directives["frame-ancestors"] == "'none'"


def test_spa_assets_are_cacheable_but_shell_is_not_public(make, web_dist):
    _, client = make()
    asset = client.get("/app-assets/assets/app-hash.js")
    assert asset.status_code == 200 and asset.text == "export {};"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert asset.headers["x-content-type-options"] == "nosniff"
    unchanged = client.get("/app-assets/assets/app-hash.js", headers={"if-none-match": asset.headers["etag"]})
    assert unchanged.status_code == 304 and unchanged.headers["cache-control"] == asset.headers["cache-control"]
    assert client.get("/app-assets/index.html").status_code == 404
    assert client.get("/app-assets/assets/..%2Findex.html").status_code == 404
    assert client.get("/other", headers=OWNER).status_code == 404


def test_missing_build_does_not_prevent_classic_or_mcp_starting(make, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "APP_DIST", tmp_path / "absent")
    _, client = make()
    assert client.get("/", headers=OWNER).status_code == 503
    assert client.get("/classic", headers=OWNER).status_code == 200
    assert client.get("/healthz").status_code == 200
    assert client.get("/mcp").status_code == 401
    assert client.get("/inbox/poll", headers=OWNER).status_code == 401
    assert client.get("/inbox/events", headers=OWNER).status_code == 401


def test_login_shell_is_available_when_signed_out(make, web_dist):
    _, client = make()
    response = client.get("/login")
    assert response.status_code == 200
    assert response.text == (web_dist / "index.html").read_text()
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "same-origin"
    signed_in = client.get("/login", params={"next": "/agents?tab=known"}, headers=OWNER)
    assert signed_in.status_code == 303 and signed_in.headers["location"] == "/agents?tab=known"


def test_auth_session_matches_access_and_reuses_classic_csrf(make):
    _, client = make()
    fields = {"methods": {"passkeys": 0, "oidc": None, "access": True}}
    anonymous = client.get("/auth/session")
    assert anonymous.status_code == 200
    assert anonymous.json() == {**fields, "signed_in": False, "via": None, "csrf": None}
    assert anonymous.headers["cache-control"] == "no-store"
    session = client.get("/auth/session", headers=OWNER)
    token = session.json()["csrf"]
    assert session.json() == {**fields, "signed_in": True, "via": "access", "csrf": token} and len(token) == 64
    assert session.headers["cache-control"] == "no-store"
    classic = client.get("/classic", headers=OWNER)
    assert re.search(r'name="csrf" value="([0-9a-f]+)"', classic.text)[1] == token
    assert client.get("/api/session", headers=OWNER).status_code == 404


def test_auth_session_reports_when_access_is_not_configured(make):
    _, client = make(config=base_config(auth={"owner_emails": ["owner@example.test"]}))
    response = client.get("/auth/session", headers=OWNER)
    assert response.status_code == 200
    assert response.json() == {"signed_in": False, "via": None, "csrf": None,
                               "methods": {"passkeys": 0, "oidc": None, "access": False}}


def test_api_owner_and_csrf_are_checked_before_dispatch(make):
    _, client = make()
    token = client.get("/auth/session", headers=OWNER).json()["csrf"]
    target = "/api/unknown"
    denied = client.get(target)
    assert denied.status_code == 401 and denied.json() == {"error": "signed_out"}
    assert client.get(target, headers=OWNER).json() == {"error": "not_found"}
    assert client.post(target, headers={**OWNER, "origin": PUBLIC, "x-swb-csrf": token}).json() == {"error": "csrf"}
    assert client.post(target, headers={**OWNER, "origin": PUBLIC + ".invalid", "x-csrf-token": token}).json() == {"error": "csrf"}
    headers = [*OWNER.items(), ("origin", PUBLIC), ("origin", "null"), ("x-csrf-token", token)]
    assert client.post(target, headers=headers).json() == {"error": "csrf"}
    assert client.post(target, headers={**OWNER, "origin": PUBLIC, "x-csrf-token": token}).status_code == 404
