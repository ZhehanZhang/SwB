from starlette.routing import Route
from starlette.testclient import TestClient

from conftest import OWNER, PUBLIC, STATIC_KEY, Clock, base_config, consent, consent_csrf, register
from switchboard.claims import CLAIM_TTL_S


def test_consent_uses_shared_assets_and_strict_csp(make):
    _, c = make()
    cid = register(c)["client_id"]
    page, _ = consent(c, cid)
    assert "/assets/console-tokens.css" in page.text and "/assets/claim.css" in page.text
    assert "site-arc" not in page.text and "<title>Connect Claude to SwB</title>" in page.text
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "style=" not in page.text
    css = c.get("/assets/console-tokens.css")
    assert css.status_code == 200 and "#ffffff" in css.text and "#09090b" in css.text
    assert c.get("/assets/inter.woff2").headers["content-type"] == "font/woff2"
    assert c.get("/assets/nope.js").status_code == 404


def test_consent_names_the_client_and_preserves_both_submit_buttons(make):
    _, c = make()
    callback = "https://agent.meta.ai/api/hatch/oauth/callback"
    client = register(c, redirect=callback, client_name="Muse")
    page, req = consent(c, client["client_id"], redirect=callback)
    assert "<h1>Connect Muse to SwB</h1>" in page.text
    assert "Returns to <code>agent.meta.ai</code>" in page.text
    assert "Signed in as owner@example.test" in page.text
    assert "/avatars/muse.png" in page.text
    assert '<button type="submit" class="button primary" name="decision" value="approve">Approve</button>' in page.text
    assert '<button type="submit" class="button secondary" name="decision" value="deny" formnovalidate>Deny</button>' in page.text
    assert req and consent_csrf(page)
    assert "Requested scopes" not in page.text


def test_consent_uses_unknown_mark_and_only_shows_nondefault_scope_sets(make):
    _, c = make()
    callback = "http://localhost:8765/callback"
    page, _ = consent(c, register(c, redirect=callback, client_name="Muse")["client_id"], redirect=callback)
    assert "/assets/claim-connectors.svg#agent" in page.text and "/avatars/muse.png" not in page.text
    assert "Requested scopes" not in c.get(page.request.url.copy_remove_param("scope"), headers=OWNER).text
    custom = c.get(page.request.url.copy_set_param("scope", "switchboard memory.read memory.read"), headers=OWNER)
    assert custom.status_code == 200 and "Requested scopes" in custom.text
    assert "<code>switchboard</code>" in custom.text and custom.text.count("<code>memory.read</code>") == 1


def test_browser_missing_page_uses_shell_and_health_stays_json(make):
    _, c = make()
    browser = {"accept": "text/html,application/xhtml+xml,*/*"}
    missing = c.get("/missing-page", headers=browser)
    assert missing.status_code == 404 and "<h1>Page unavailable</h1>" in missing.text
    assert c.get("/missing-page").text == "Not Found"
    assert c.get("/missing-page", headers={"accept": "text/html;q=0, */*"}).text == "Not Found"
    assert c.get("/healthz", headers=browser).headers["content-type"] == "application/json"


def test_access_assertion_does_not_sign_in_when_access_is_unconfigured(make):
    cfg = base_config()
    cfg["auth"] = {"owner_emails": ["owner@example.test"]}
    _, c = make(config=cfg)
    for path, next_path in (("/", "%2F"), ("/tokens", "%2Ftokens")):
        refused = c.get(path, headers=OWNER, follow_redirects=False)
        assert refused.status_code == 303 and refused.headers["location"] == f"/login?next={next_path}"


def test_consent_shows_the_whole_mailbox_address_and_hides_known_ones(make):
    config = base_config()
    address = "northwind-procurement-assistant-staging-environment-" + "x" * 40
    config["auth"]["source_names"] = {"northwind": address}
    _, c = make(config=config)
    callback = "http://localhost:8765/callback"
    name = "Northwind procurement assistant staging environment"
    page, _ = consent(c, register(c, redirect=callback, client_name=name)["client_id"], redirect=callback)
    assert f'Mailbox <code>agent:{address}</code>' in page.text
    known, _ = consent(c, register(c)["client_id"])
    assert 'class="mailbox"' not in known.text


def test_browser_errors_use_the_shell_and_api_errors_stay_json(make):
    _, c = make()
    browser = {"accept": "text/html"}
    missing = c.get("/missing-page", headers=browser)
    assert missing.status_code == 404 and "<h1>Page unavailable</h1>" in missing.text
    assert "text/html" in missing.headers["content-type"] and missing.headers["content-security-policy"]
    assert c.get("/missing-page").text == "Not Found"
    assert c.get("/api/missing", headers={**browser, **OWNER}).json() == {"error": "not_found"}
    assert c.get("/mcp/missing", headers=browser).headers["content-type"].startswith("application/json")

    async def boom(request):
        raise RuntimeError("Fixture error")

    c.app.starlette.router.routes[:0] = [Route(path, boom) for path in ("/boom", "/api/boom", "/mcp/boom")]
    quiet = TestClient(c.app, raise_server_exceptions=False, base_url=PUBLIC)
    failed = quiet.get("/boom", headers=browser)
    assert failed.status_code == 500 and "<h1>Try again shortly</h1>" in failed.text
    assert "Fixture error" not in failed.text
    assert quiet.get("/boom").text == "Internal Server Error"
    for path in ("/api/boom", "/mcp/boom"):
        result = quiet.get(path, headers={**browser, **OWNER, "authorization": f"Bearer {STATIC_KEY}"})
        assert result.status_code == 500 and result.json() == {"error": "internal_server_error"}


def test_expired_claim_link_says_so_before_sign_in(make):
    clock = Clock()
    core, c = make(clock=clock)
    link = core.claims.mint("EXAMPLE_TOKEN", PUBLIC).removeprefix(PUBLIC)
    signed_out = c.get(link)
    assert signed_out.status_code == 303 and signed_out.headers["location"].startswith("/login")
    clock.t += CLAIM_TTL_S + 1
    expired = c.get(link)
    assert expired.status_code == 410 and "<h1>Link expired</h1>" in expired.text
