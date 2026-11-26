from conftest import CAPTAIN, OWNER, base_config, consent, register


def test_pages_use_site_assets_and_strict_csp(make):
    _, c = make()
    cid = register(c)["client_id"]
    page, _ = consent(c, cid)
    assert "/assets/bridge.css" in page.text and "site-arc" in page.text and "<title>Connect | Switchboard</title>" in page.text
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "style=" not in page.text
    css = c.get("/assets/bridge.css")
    assert css.status_code == 200 and "#f5f5f4" in css.text and "#1c1917" in css.text
    assert c.get("/assets/nope.js").status_code == 404


def test_admin_page_is_the_root_and_needs_the_owners_access_identity(make):
    core, c = make(env={"SWITCHBOARD_KEY_CAPY": "s" * 32})
    core.projects.create(slug="infra", name="Infra", links={"repos": ["example-org/links"]})
    for headers in ({}, {"cf-access-jwt-assertion": "other"}):
        redirect = c.get("/", headers=headers)
        assert redirect.status_code == 303 and redirect.headers["location"] == "/login?next=%2F"
    page = c.get("/", headers=OWNER)
    assert page.status_code == 200 and "style=" not in page.text and 'type="password"' not in page.text
    for needle in ("Signed in as owner@example.test", "global route", CAPTAIN, "Infra", "CAPY_API_KEY", "Bearer keys"):
        assert needle in page.text, needle
    assert c.get("/routes").status_code == 404
    assert c.get("/healthz").status_code == 200


def test_access_assertion_does_not_sign_in_when_access_is_unconfigured(make):
    cfg = base_config()
    cfg["auth"] = {"owner_emails": ["owner@example.test"]}
    _, c = make(config=cfg)
    refused = c.get("/", headers=OWNER)
    assert refused.status_code == 303 and refused.headers["location"] == "/login?next=%2F"
