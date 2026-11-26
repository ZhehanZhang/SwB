import pytest

from conftest import OWNER, PUBLIC, STATIC_KEY
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


@pytest.mark.parametrize("path", ["/", "/canvas", "/agents", "/inbox", "/provision", "/projects", "/memory", "/audit",
                                  "/agents/client/setup", "/inbox/thread/one", "/canvas/"])
def test_spa_paths_require_owner_and_serve_uncached_shell(make, web_dist, path):
    _, client = make()
    assert client.get(path).status_code == 403
    assert client.get(path, headers={"authorization": f"Bearer {STATIC_KEY}"}).status_code == 403
    response = client.get(path, headers=OWNER)
    assert response.status_code == 200
    assert response.text == (web_dist / "index.html").read_text()
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("text/html")
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


async def two_sessions(assertion):
    if assertion in ("good", "renewed"):
        return "owner@example.test"
    return None


def test_api_owner_and_csrf_are_checked_before_dispatch(make):
    _, client = make(access=two_sessions)
    anonymous = client.get("/api/session")
    assert anonymous.status_code == 403 and anonymous.json() == {"error": "owner_required"}
    assert anonymous.headers["cache-control"] == "no-store"
    session = client.get("/api/session", headers=OWNER)
    token = session.json()["csrf"]
    assert session.json() == {"email": "owner@example.test", "csrf": token} and len(token) == 64
    assert session.headers["cache-control"] == "no-store"
    assert client.get("/api/session", headers=OWNER).json()["csrf"] == token
    renewed = {"cf-access-jwt-assertion": "renewed"}
    assert client.get("/api/session", headers=renewed).json()["csrf"] != token
    target = "/api/unknown"
    assert client.get(target).status_code == 403
    assert client.get(target, headers=OWNER).json() == {"error": "not_found"}
    assert client.post(target, headers={**renewed, "origin": PUBLIC, "x-swb-csrf": token}).json() == {"error": "csrf_failed"}
    assert client.post(target, headers={**OWNER, "origin": PUBLIC + ".invalid", "x-swb-csrf": token}).json() == {"error": "csrf_failed"}
    headers = [*OWNER.items(), ("origin", PUBLIC), ("origin", PUBLIC), ("x-swb-csrf", token)]
    assert client.post(target, headers=headers).json() == {"error": "csrf_failed"}
    assert client.post(target, headers={**OWNER, "origin": PUBLIC, "x-swb-csrf": token}).status_code == 404
