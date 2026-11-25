from urllib.parse import parse_qs, urlparse

from conftest import CLAUDE_CB, OWNER, PUBLIC, STATIC_KEY, base_config, call, consent, payload, pkce, register, rpc, sign_in, Clock


def test_metadata_advertises_what_claude_and_chatgpt_need(make):
    _, c = make()
    prm = c.get("/.well-known/oauth-protected-resource").json()
    assert prm["resource"] == PUBLIC + "/mcp" and prm["authorization_servers"] == [PUBLIC]
    assert c.get("/.well-known/oauth-protected-resource/mcp").json() == prm
    asm = c.get("/.well-known/oauth-authorization-server").json()
    assert asm["issuer"] == PUBLIC
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert asm["client_id_metadata_document_supported"] is True and "none" in asm["token_endpoint_auth_methods_supported"]
    assert asm["registration_endpoint"] == PUBLIC + "/register"
    assert asm["authorization_response_iss_parameter_supported"] is True


def test_mcp_without_token_is_401_with_discovery_challenge(make):
    _, c = make()
    r = rpc(c, None, "tools/list")
    assert r.status_code == 401
    assert f'resource_metadata="{PUBLIC}/.well-known/oauth-protected-resource"' in r.headers["www-authenticate"]
    bad = rpc(c, "not-a-token", "tools/list")
    assert bad.status_code == 401 and 'error="invalid_token"' in bad.headers["www-authenticate"]


def test_full_flow_issues_token_tagged_with_the_assistant(make):
    _, c = make()
    tok = sign_in(c)
    assert tok["token_type"] == "Bearer" and tok["expires_in"] == 3600
    me = payload(call(c, tok["access_token"], "switchboard_status"))
    assert me["you"] == {"agent": "claude", "auth": "oauth", "origin": "mcp"}
    gpt = sign_in(c, redirect="https://chatgpt.com/connector_platform_oauth_redirect")
    assert payload(call(c, gpt["access_token"], "switchboard_status"))["you"]["agent"] == "chatgpt"


def test_approving_needs_the_owners_access_jwt_on_the_post_too(make):
    _, c = make()
    cid = register(c)["client_id"]
    _, req = consent(c, cid)
    assert c.post("/authorize", data={"req": req, "decision": "approve"}).status_code == 403
    assert c.post("/authorize", data={"req": req, "decision": "approve"}, headers={"cf-access-jwt-assertion": "other"}).status_code == 403
    assert c.post("/authorize", data={"req": req, "decision": "approve"}, headers=OWNER).status_code == 302


def test_without_access_sign_in_is_off_and_there_is_no_password_form(make):
    cfg = base_config()
    cfg["auth"] = {"owner_emails": ["owner@example.test"]}
    _, c = make(config=cfg)
    cid = register(c)["client_id"]
    page, req = consent(c, cid)
    assert page.status_code == 503 and req is None and 'type="password"' not in page.text
    assert any("cloudflare_access" in p for p in c.get("/healthz").json()["problems"])


def test_deny_redirects_with_access_denied_and_iss(make):
    _, c = make()
    cid = register(c)["client_id"]
    page, req = consent(c, cid)
    r = c.post("/authorize", data={"req": req, "decision": "deny"}, headers=OWNER)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["error"] == ["access_denied"] and q["iss"] == [PUBLIC]


def test_tampered_form_token_is_rejected(make):
    _, c = make()
    cid = register(c)["client_id"]
    _, req = consent(c, cid)
    r = c.post("/authorize", data={"req": req[:-3] + "AAA", "decision": "approve"}, headers=OWNER)
    assert r.status_code == 400


def test_registration_rejects_foreign_redirects(make):
    _, c = make()
    r = c.post("/register", json={"redirect_uris": ["https://evil.example/cb"]})
    assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri"
    assert c.post("/register", json={"redirect_uris": ["http://claude.ai/api/mcp/auth_callback"]}).status_code == 400
    ok = c.post("/register", json={"redirect_uris": ["http://localhost:33418/callback"], "client_name": "Claude Code"})
    assert ok.status_code == 201


def test_authorize_rejects_unregistered_redirect_and_missing_pkce(make):
    _, c = make()
    cid = register(c)["client_id"]
    page, _ = consent(c, cid, redirect="https://chatgpt.com/connector_platform_oauth_redirect")
    assert page.status_code == 400
    r = c.get("/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": CLAUDE_CB, "state": "s"})
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert r.status_code == 302 and q["error"] == ["invalid_request"]


def test_pkce_mismatch_and_code_reuse_fail(make):
    _, c = make()
    cid = register(c)["client_id"]
    from conftest import authorize

    verifier, challenge = pkce()
    r = authorize(c, cid, challenge=challenge)
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    bad = c.post("/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid, "code_verifier": pkce()[0], "redirect_uri": CLAUDE_CB})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant"
    again = c.post("/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid, "code_verifier": verifier, "redirect_uri": CLAUDE_CB})
    assert again.status_code == 400


def test_refresh_rotates_and_reuse_after_grace_revokes_family(make):
    clock = Clock()
    _, c = make(clock=clock)
    tok = sign_in(c)
    r1 = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": tok["client_id"]})
    assert r1.status_code == 200
    new = r1.json()
    assert rpc(c, new["access_token"], "tools/list").status_code == 200
    clock.t += 120
    replay = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": tok["client_id"]})
    assert replay.status_code == 400
    assert rpc(c, new["access_token"], "tools/list").status_code == 401


def test_access_token_expires(make):
    clock = Clock()
    _, c = make(clock=clock)
    tok = sign_in(c)
    clock.t += 3601
    assert rpc(c, tok["access_token"], "tools/list").status_code == 401


def test_confidential_dcr_client_needs_its_secret(make):
    _, c = make()
    reg = register(c, token_endpoint_auth_method="client_secret_post")
    from conftest import authorize

    verifier, challenge = pkce()
    code = parse_qs(urlparse(authorize(c, reg["client_id"], challenge=challenge).headers["location"]).query)["code"][0]
    form = {"grant_type": "authorization_code", "code": code, "client_id": reg["client_id"], "code_verifier": verifier}
    assert c.post("/token", data=form).status_code == 401
    assert c.post("/token", data={**form, "client_secret": reg["client_secret"]}).status_code in (200, 400)


def test_cimd_client(make, cimd):
    url = "https://claude.ai/oauth/mcp-oauth-client-metadata"
    cimd.docs[url] = {"client_id": url, "client_name": "Claude", "redirect_uris": [CLAUDE_CB], "token_endpoint_auth_method": "none"}
    _, c = make()
    tok = sign_in(c, client_id=url)
    assert payload(call(c, tok["access_token"], "switchboard_status"))["you"]["agent"] == "claude"


def test_cimd_rejects_untrusted_host_and_mismatched_doc(make, cimd):
    _, c = make()
    page, _ = consent(c, "https://evil.example/client.json")
    assert page.status_code == 400 and cimd.fetches == 0
    url = "https://chatgpt.com/oauth/client.json"
    cimd.docs[url] = {"client_id": "https://chatgpt.com/other", "redirect_uris": [CLAUDE_CB]}
    page, _ = consent(c, url)
    assert page.status_code == 400


def test_static_key_for_capy(make):
    _, c = make()
    assert payload(call(c, STATIC_KEY, "switchboard_status"))["you"] == {"agent": "capy", "auth": "key", "origin": "mcp"}
    assert rpc(c, STATIC_KEY[:-1] + "t", "tools/list").status_code == 401


def test_cloudflare_access_gate(make):
    _, c = make()
    cid = register(c)["client_id"]
    assert consent(c, cid, headers={})[0].status_code == 403
    assert consent(c, cid, headers={"cf-access-jwt-assertion": "other"})[0].status_code == 403
    page, req = consent(c, cid, headers={"cf-access-jwt-assertion": "good"})
    assert page.status_code == 200 and 'type="password"' not in page.text and "SSO as owner@example.test" in page.text
    r = c.post("/authorize", data={"req": req, "decision": "approve"}, headers={"cf-access-jwt-assertion": "good"})
    assert r.status_code == 302 and "code=" in r.headers["location"]


def test_no_public_url_disables_oauth_but_keeps_keys(make):
    cfg = base_config()
    cfg.pop("public_url")
    _, c = make(config=cfg)
    assert c.get("/.well-known/oauth-authorization-server").status_code == 404
    assert c.post("/register", json={"redirect_uris": [CLAUDE_CB]}).status_code == 404
    assert rpc(c, STATIC_KEY, "tools/list").status_code == 200
