from conftest import OWNER, base_config, consent, register


def test_pages_use_site_assets_and_strict_csp(make):
    _, c = make()
    cid = register(c)["client_id"]
    page, _ = consent(c, cid)
    assert "/assets/bridge.css" in page.text and "site-arc" in page.text and "<title>Connect | SwB</title>" in page.text
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "style=" not in page.text
    css = c.get("/assets/bridge.css")
    assert css.status_code == 200 and "#f5f5f4" in css.text and "#1c1917" in css.text
    assert c.get("/assets/nope.js").status_code == 404


def test_access_assertion_does_not_sign_in_when_access_is_unconfigured(make):
    cfg = base_config()
    cfg["auth"] = {"owner_emails": ["owner@example.test"]}
    _, c = make(config=cfg)
    for path, next_path in (("/", "%2F"), ("/tokens", "%2Ftokens")):
        refused = c.get(path, headers=OWNER, follow_redirects=False)
        assert refused.status_code == 303 and refused.headers["location"] == f"/login?next={next_path}"
