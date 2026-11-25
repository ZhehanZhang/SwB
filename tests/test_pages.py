from conftest import register, consent


def test_pages_use_site_assets_and_strict_csp(make):
    _, c = make()
    cid = register(c)["client_id"]
    page, _ = consent(c, cid)
    assert "/assets/bridge.css" in page.text and "site-arc" in page.text and "brand-sig" in page.text
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "style=" not in page.text
    css = c.get("/assets/bridge.css")
    assert css.status_code == 200 and "#f5f5f4" in css.text and "#1c1917" in css.text
    assert "siteAppearance" in c.get("/assets/prefs.js").text
    assert c.get("/assets/nope.js").status_code == 404
