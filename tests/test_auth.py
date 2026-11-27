from urllib.parse import parse_qs, urlparse

import pytest

from conftest import CLAUDE_CB, OWNER, PUBLIC, STATIC_KEY, authorize, base_config, call, consent, consent_csrf, payload, pkce, register, rpc, sign_in, Clock
from switchboard.agents import Agents
from switchboard.config import ConfigError


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


@pytest.mark.parametrize("host", ["grok.com", "x.ai"])
def test_grok_dynamic_registration_and_oauth_source(make, host):
    _, client = make()
    redirect = f"https://{host}/connectors/oauth/callback"
    reg = register(client, redirect=redirect, client_name="Custom connector")
    page, _ = consent(client, reg["client_id"], redirect=redirect + "/other")
    assert page.status_code == 400
    token = sign_in(client, redirect=redirect, client_id=reg["client_id"])
    who = payload(call(client, token["access_token"], "switchboard_status"))["you"]
    assert who == {"agent": "grok", "auth": "oauth", "origin": "mcp"}


@pytest.mark.parametrize("redirect", ["https://grok.com.example.test/callback", "https://grok.com@other.example.test/callback",
                                      "https://other.example.test@grok.com/callback", "http://grok.com/callback"])
def test_grok_redirect_allowlist_rejects_other_origins(make, redirect):
    _, client = make()
    response = client.post("/register", json={"redirect_uris": [redirect]})
    assert response.status_code == 400 and response.json()["error"] == "invalid_redirect_uri"


def test_grok_metadata_document_oauth(make, cimd):
    _, client = make()
    url = "https://x.ai/mcp/metadata.json"
    redirect = "https://grok.com/connectors/oauth/callback"
    cimd.docs[url] = {"client_id": url, "redirect_uris": [redirect]}
    token = sign_in(client, redirect=redirect, client_id=url)
    assert payload(call(client, token["access_token"], "switchboard_status"))["you"]["agent"] == "grok"
    assert cimd.fetches == 1


@pytest.mark.parametrize(("agent", "url", "registered", "actual"), [
    ("claude-code", "https://claude.ai/oauth/claude-code-client-metadata", "http://localhost/callback", "http://localhost:3456/callback"),
    ("codex", "https://chatgpt.com/oauth/codex/client.json", "http://127.0.0.1/callback", "http://127.0.0.1:3456/callback"),
    ("codex", "https://chatgpt.com/oauth/codex/example-id/client.json", "http://127.0.0.1/callback", "http://127.0.0.1:3456/callback"),
    ("goose", "https://goose-docs.ai/oauth/client-metadata.json", "http://[::1]/oauth_callback", "http://[::1]:3456/oauth_callback"),
    ("opencode", "https://opencode.ai/oauth/opencode/client.json", "http://127.0.0.1/callback", "http://127.0.0.1:3456/callback"),
])
def test_coding_agent_cimd_signs_in_as_the_right_mailbox(make, cimd, agent, url, registered, actual):
    _, client = make()
    cimd.docs[url] = {"client_id": url, "redirect_uris": [registered]}
    token = sign_in(client, redirect=actual, client_id=url)
    assert payload(call(client, token["access_token"], "switchboard_status"))["you"]["agent"] == agent


def test_new_cimd_allows_only_the_native_document_by_default(make, cimd):
    _, client = make()
    page, _ = consent(client, "https://opencode.ai/other", redirect="http://127.0.0.1:3456/callback")
    assert page.status_code == 400 and cimd.fetches == 0


def test_codex_cimd_pattern_follows_operator_agent_configuration(make, cimd):
    _, client = make(base_config(agents={"chatgpt": {}, "review-agent": {"preset": "codex"}}))
    url = "https://chatgpt.com/oauth/codex/test-client/client.json"
    redirect = "http://127.0.0.1/callback"
    cimd.docs[url] = {"client_id": url, "client_name": "Codex", "redirect_uris": [redirect]}
    token = sign_in(client, redirect="http://127.0.0.1:3456/callback", client_id=url)
    assert payload(call(client, token["access_token"], "switchboard_status"))["you"]["agent"] == "review-agent"


def test_invalid_cimd_pattern_fails_at_config_load():
    pattern = "https://example.test/["
    with pytest.raises(ConfigError) as error:
        Agents.build({"example": {"match": {"cimd_patterns": [pattern]}}}, [])
    assert "agents.example.match.cimd_patterns" in str(error.value)
    assert repr(pattern) in str(error.value)


def test_approving_needs_the_owners_access_jwt_and_csrf_on_the_post_too(make):
    _, c = make()
    cid = register(c)["client_id"]
    page, req = consent(c, cid)
    form = {"req": req, "decision": "approve", "csrf": consent_csrf(page)}
    for headers in ({}, {"cf-access-jwt-assertion": "other"}):
        r = c.post("/authorize", data=form, headers=headers)
        assert r.status_code == 401 and r.json() == {"error": "signed_out"}
    r = c.post("/authorize", data={"req": req, "decision": "approve"}, headers=OWNER)
    assert r.status_code == 403 and r.json() == {"error": "csrf"}
    assert c.post("/authorize", data=form, headers=OWNER).status_code == 302


def test_without_access_sign_in_uses_the_login_page_and_there_is_no_password_form(make):
    cfg = base_config()
    cfg["auth"] = {"owner_emails": ["owner@example.test"]}
    _, c = make(config=cfg)
    cid = register(c)["client_id"]
    page, req = consent(c, cid)
    assert page.status_code == 303 and req is None
    login = c.get(page.headers["location"])
    assert login.status_code == 200 and 'type="password"' not in login.text
    assert c.get("/healthz").json()["oauth"] is True


def test_deny_redirects_with_access_denied_and_iss(make):
    _, c = make()
    cid = register(c)["client_id"]
    page, req = consent(c, cid)
    r = c.post("/authorize", data={"req": req, "decision": "deny", "csrf": consent_csrf(page)}, headers=OWNER)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["error"] == ["access_denied"] and q["iss"] == [PUBLIC]


def test_tampered_form_token_is_rejected(make):
    _, c = make()
    cid = register(c)["client_id"]
    page, req = consent(c, cid)
    r = c.post("/authorize", data={"req": req[:-3] + "AAA", "decision": "approve", "csrf": consent_csrf(page)}, headers=OWNER)
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


def test_chatgpt_cimd_negotiates_public_client(make, cimd):
    url = "https://chatgpt.com/oauth/client.json"
    cb = "https://chatgpt.com/connector_platform_oauth_redirect"
    cimd.docs[url] = {"client_id": url, "client_name": "ChatGPT", "redirect_uris": [cb], "token_endpoint_auth_method": "private_key_jwt", "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"]}
    _, c = make()
    tok = sign_in(c, redirect=cb, client_id=url)
    assert payload(call(c, tok["access_token"], "switchboard_status"))["you"]["agent"] == "chatgpt"
    verifier, challenge = pkce()
    code = parse_qs(urlparse(authorize(c, url, cb, challenge=challenge).headers["location"]).query)["code"][0]
    assert c.post("/token", data={"grant_type": "authorization_code", "code": code, "client_id": url, "code_verifier": "wrong" + verifier}).status_code == 400


def test_cimd_rejects_confidential_only_client(make, cimd):
    url = "https://chatgpt.com/oauth/client.json"
    cimd.docs[url] = {"client_id": url, "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"], "token_endpoint_auth_method": "private_key_jwt"}
    _, c = make()
    assert consent(c, url, "https://chatgpt.com/connector_platform_oauth_redirect")[0].status_code == 400


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
    for headers in ({}, {"cf-access-jwt-assertion": "other"}):
        refused, _ = consent(c, cid, headers=headers)
        assert refused.status_code == 303
        assert parse_qs(urlparse(refused.headers["location"]).query)["next"][0].startswith("/authorize?")
    page, req = consent(c, cid, headers={"cf-access-jwt-assertion": "good"})
    assert page.status_code == 200 and 'type="password"' not in page.text and "Signed in as owner@example.test" in page.text
    r = c.post("/authorize", data={"req": req, "decision": "approve", "csrf": consent_csrf(page)}, headers=OWNER)
    assert r.status_code == 302 and "code=" in r.headers["location"]


def test_no_public_url_disables_oauth_but_keeps_keys(make):
    cfg = base_config()
    cfg.pop("public_url")
    _, c = make(config=cfg)
    assert c.get("/.well-known/oauth-authorization-server").status_code == 404
    assert c.post("/register", json={"redirect_uris": [CLAUDE_CB]}).status_code == 404
    assert rpc(c, STATIC_KEY, "tools/list").status_code == 200


def test_capy_signs_in_with_oauth_as_agent_capy(make):
    core, c = make()
    cb = "https://capy.ai/web/callbacks/mcp"
    cid = register(c, redirect=cb, client_name="Capy MCP client")["client_id"]
    page, _ = consent(c, cid, redirect=cb)
    assert page.status_code == 200 and "<b>Capy</b> wants to connect as" in page.text and ">capy</span>" in page.text
    tok = sign_in(c, redirect=cb)
    me = payload(call(c, tok["access_token"], "switchboard_status"))["you"]
    assert me["agent"] == "capy" and me["auth"] == "oauth"
    assert payload(call(c, STATIC_KEY, "switchboard_status"))["you"]["agent"] == "capy"


def test_key_scopes_apply_to_an_oauth_agent_too(make):
    cfg = base_config()
    cfg["auth"]["key_scopes"] = {"capy": ["project:orbit"]}
    core, c = make(config=cfg)
    tok = sign_in(c, redirect="https://capy.ai/web/callbacks/mcp")
    assert payload(call(c, tok["access_token"], "switchboard_status"))["you"]["scopes"] == ["project:orbit"]


def test_capy_metadata_document_client_is_allowed_by_default(make, cimd):
    url = "https://capy.ai/.well-known/oauth-client/mcp"
    cb = "https://capy.ai/web/callbacks/mcp"
    cimd.docs[url] = {"client_id": url, "client_name": "Capy", "redirect_uris": [cb], "token_endpoint_auth_method": "none"}
    _, c = make()
    tok = sign_in(c, redirect=cb, client_id=url)
    assert payload(call(c, tok["access_token"], "switchboard_status"))["you"]["agent"] == "capy"


def test_a_refused_redirect_is_named_on_the_page_and_in_the_audit_log(make):
    core, c = make()
    cid = register(c)["client_id"]
    bad = "https://evil.example/cb"
    page, _ = consent(c, cid, redirect=bad)
    assert page.status_code == 400 and bad in page.text
    row = core.audit.recent(action="oauth_refused")[0]
    assert bad in row["detail"] and row["target"] == "/authorize" and row["ref"] == cid
    r = c.post("/register", json={"redirect_uris": ["https://other.example/cb"], "client_name": "x"})
    assert r.status_code == 400 and "https://other.example/cb" in core.audit.recent(action="oauth_refused")[0]["detail"]
