import base64
import hashlib
import json
import logging
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import cbor2
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from conftest import (
    OWNER, PUBLIC, STATIC_KEY, Clock, base_config, call, consent, consent_csrf,
    default_env, error_text, payload, register, rpc,
)
from switchboard import web
from switchboard.auth import b64url
from switchboard.config import ConfigError
from switchboard.oidc import COOKIE as OIDC_COOKIE
from switchboard.sessions import CHALLENGE_COOKIE, LINK_HISTORY_TTL, SESSION_COOKIE, SESSION_IDLE, SESSION_LIFETIME, Sessions, safe_next
from switchboard.store import Store

SIGN_IN = "/auth/passkey/login"
ADD = "/auth/passkey/register"
ISSUER = "https://identity.example.test"
OWNER_EMAIL = "owner@example.test"
OIDC_CONFIG = {"issuer": ISSUER, "client_id": "switchboard-client", "client_secret_env": "OIDC_SECRET",
               "name": "Example Identity", "owner_subjects": ["owner-subject"]}
OIDC_ENV = {**default_env(), "OIDC_SECRET": "fixture-client-secret"}


@pytest.fixture
def no_spa_build(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "APP_DIST", tmp_path / "no-spa")


def token(client):
    result = payload(call(client, STATIC_KEY, "login_link"))
    assert set(result) == {"url", "expires_in"} and result["expires_in"] == 300
    assert result["url"].startswith(f"{PUBLIC}/login#t=")
    return result["url"].split("#t=", 1)[1]


def sign_in(client):
    return client.post("/auth/link", json={"token": token(client)})


def csrf(client, headers=None):
    return {"X-CSRF-Token": client.get("/auth/session", headers=headers).json()["csrf"]}


def security_headers(response):
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def cookie_attributes(response, name, ttl):
    cookie = next(h for h in response.headers.get_list("set-cookie") if h.startswith(name + "=")).lower()
    assert all(f"; {a}" in cookie for a in ("httponly", "secure", "samesite=lax", "path=/", f"max-age={ttl}"))
    assert "; domain=" not in cookie


def test_owner_session_shape_cookie_rotation_and_logout(make):
    core, client = make(base_config(auth={}))
    assert client.get("/auth/session").json() == {
        "signed_in": False, "via": None, "csrf": None, "methods": {"passkeys": 0, "oidc": None, "access": False},
    }
    refusal = client.get("/auth/passkeys")
    assert refusal.status_code == 401 and refusal.json() == {"error": "signed_out"}
    security_headers(refusal)
    response = sign_in(client)
    assert response.status_code == 204 and response.content == b""
    security_headers(response)
    cookie_attributes(response, SESSION_COOKIE, SESSION_LIFETIME)
    initial = client.cookies[SESSION_COOKIE]
    digest = hashlib.sha256(initial.encode()).hexdigest()
    assert len(initial) == 43 and len(base64.urlsafe_b64decode(initial + "=")) == 32
    stored = core.store.one("SELECT * FROM owner_sessions")
    assert stored["id"] == digest and initial not in str(dict(stored))
    first_session = client.get("/auth/session")
    security_headers(first_session)
    assert first_session.json() == {"signed_in": True, "via": "link", "csrf": core.auth.csrf(digest),
                                   "methods": {"passkeys": 0, "oidc": None, "access": False}}
    assert client.get("/auth/passkeys").json() == []
    assert sign_in(client).status_code == 204
    assert core.store.one("SELECT 1 FROM owner_sessions WHERE id = ?", (digest,)) is None
    new = client.cookies[SESSION_COOKIE]
    assert new != initial and csrf(client)["X-CSRF-Token"] != first_session.json()["csrf"]
    refused = client.post("/auth/logout", headers={"X-CSRF-Token": first_session.json()["csrf"]})
    assert refused.status_code == 403 and refused.json() == {"error": "csrf"}
    response = client.post("/auth/logout", headers=csrf(client))
    assert response.status_code == 204 and core.store.one("SELECT 1 FROM owner_sessions") is None
    cookie_attributes(response, SESSION_COOKIE, 0)
    client.cookies.set(SESSION_COOKIE, new)
    assert client.get("/auth/session").json()["signed_in"] is False


@pytest.mark.parametrize("absolute", [False, True])
def test_session_last_seen_throttle_and_expiration(make, absolute):
    clock = Clock()
    core, client = make(clock=clock)
    start = clock.t
    assert sign_in(client).status_code == 204
    clock.t += 59
    assert client.get("/auth/session").json()["signed_in"] is True
    assert core.store.one("SELECT last_seen FROM owner_sessions")["last_seen"] == start
    clock.t += 1
    assert client.get("/auth/session").json()["signed_in"] is True
    assert core.store.one("SELECT last_seen FROM owner_sessions")["last_seen"] == start + 60
    if absolute:
        for day in range(6, 30, 6):
            clock.t = start + day * 86400
            assert client.get("/auth/session").json()["signed_in"] is True
        clock.t = start + SESSION_LIFETIME - 1
        assert client.get("/auth/session").json()["signed_in"] is True
        clock.t += 1
    else:
        clock.t += SESSION_IDLE
    assert client.get("/auth/session").json()["signed_in"] is False


def test_owner_csrf_and_origin_with_header_form_and_access_priority(make):
    core, client = make()
    assert sign_in(client).status_code == 204
    access_session = client.get("/auth/session", headers=OWNER).json()
    assert access_session["via"] == "access"
    assert client.get("/auth/session", headers={"cf-access-jwt-assertion": "other"}).json()["via"] == "link"
    assert access_session["csrf"] == core.auth.csrf(OWNER_EMAIL)
    redirect = client.get("/login", params={"next": "/?show=routes"}, headers=OWNER)
    assert redirect.status_code == 303 and redirect.headers["location"] == "/?show=routes"
    endpoint, form = "/pairing/code", {"agent": "test"}
    missing = client.post(endpoint, data=form)
    assert missing.status_code == 403 and missing.json() == {"error": "csrf"}
    headers = csrf(client)
    foreign = client.post(endpoint, data=form, headers={**headers, "Origin": "https://elsewhere.example.test"})
    assert foreign.status_code == 403 and foreign.json() == {"error": "csrf"}
    assert client.post(endpoint, data=form, headers={**headers, "Origin": PUBLIC}).status_code == 200
    assert client.post(endpoint, data={**form, "csrf": access_session["csrf"]}, headers={**OWNER, "Origin": PUBLIC}).status_code == 200
    assert client.post("/auth/logout", data={"csrf": access_session["csrf"]}, headers=OWNER).status_code == 204
    assert client.get("/auth/session", headers=OWNER).json()["via"] == "access"
    assert client.get("/auth/session").json()["signed_in"] is False


def test_link_origin_single_use_expiry_and_audit(make, caplog):
    clock = Clock()
    core, client = make(clock=clock)
    with caplog.at_level(logging.DEBUG):
        first = token(client)
        assert len(first) == 43
        row = core.store.one("SELECT * FROM owner_login_links")
        assert row["token_hash"] == hashlib.sha256(first.encode()).hexdigest()
        for path in ("/auth/link", SIGN_IN + "/options", SIGN_IN + "/verify"):
            refused = client.post(path, json={"token": first}, headers={"Origin": "null"})
            assert refused.status_code == 403 and refused.json() == {"error": "csrf"}
        used = client.post("/auth/link", json={"token": first}, headers={"Origin": PUBLIC})
        assert used.status_code == 204
        assert core.store.one("SELECT 1 FROM owner_login_links") is None
        session = core.store.one("SELECT client_id, source FROM owner_sessions")
        assert dict(session) == {"client_id": "key:capy", "source": "capy"}
        for action in ("login_link_minted", "login_link_used"):
            [audit] = core.audit.recent(action=action)
            assert (audit["client"], audit["source"], audit["outcome"]) == ("key:capy", "capy", "ok")
            assert first not in str(audit)
        spent = client.post("/auth/link", json={"token": first})
        assert spent.status_code == 401 and spent.json() == {"error": "link_used"}
        assert "set-cookie" not in spent.headers
        assert dict(core.store.one("SELECT * FROM owner_login_links_used")) == {
            "token_hash": hashlib.sha256(first.encode()).hexdigest(), "expires_at": clock.t + 300,
        }
        other = token(client)
        clock.t += 300
        expired = client.post("/auth/link", json={"token": other})
        assert expired.status_code == 401 and expired.json() == {"error": "link_expired"}
        assert "set-cookie" not in expired.headers
        assert client.post("/auth/link", json={"token": first}).json() == {"error": "link_expired"}
        token(client)
        for old in (first, other):
            assert client.post("/auth/link", json={"token": old}).json() == {"error": "link_expired"}
        clock.t += LINK_HISTORY_TTL
        token(client)
        assert core.store.one("SELECT 1 FROM owner_login_links_used") is None
        assert core.store.one("SELECT 1 FROM owner_login_links WHERE expires_at <= ?",
                              (clock.t - LINK_HISTORY_TTL,)) is None
        for old in (first, other):
            assert client.post("/auth/link", json={"token": old}).json() == {"error": "link_invalid"}
    assert all(value not in caplog.text for value in (first, other, client.cookies[SESSION_COOKIE]))
    assert all(value not in str(core.audit.recent()) for value in (first, other, client.cookies[SESSION_COOKIE]))


def test_concurrent_login_link_redemption_has_one_winner(make):
    core, client = make()
    first = token(client)
    other_store = Store(core.store.path, clock=core.clock)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            wins = list(workers.map(lambda sessions: sessions.consume_link(first), (core.auth.sessions, Sessions(other_store))))
        successful, = [row for row, error in wins if error is None]
        assert successful["client_id"] == "key:capy"
        assert [error for row, error in wins if row is None] == ["link_used"]
    finally:
        other_store.close()


@pytest.mark.parametrize("restriction", [{"key_scopes": {"capy": ["general"]}},
                                          {"key_tools": {"capy": ["login_link"]}},
                                          {"key_targets": {"capy": ["*"]}}])
def test_restricted_keys_cannot_discover_or_call_login_link(make, restriction):
    core, client = make(base_config(auth=restriction))
    tools = rpc(client, STATIC_KEY, "tools/list").json()["result"]["tools"]
    assert "login_link" not in {tool["name"] for tool in tools}
    assert error_text(call(client, STATIC_KEY, "login_link"))
    paired = core.connections.mint(agent="new-agent", project=None, client=None, by="fixture")
    key = core.connections.redeem(paired["code"])["key"]
    assert "login_link" not in {tool["name"] for tool in rpc(client, key, "tools/list").json()["result"]["tools"]}
    assert error_text(call(client, key, "login_link"))
    assert core.store.one("SELECT 1 FROM owner_login_links") is None


@pytest.mark.parametrize("body", [{}, {"token": None}, {"token": 1}, {"token": "nonsense"},
                                 {"token": "!" * 43}, {"token": "a" * 43}])
def test_invalid_link_never_starts_a_session(make, body):
    core, client = make()
    response = client.post("/auth/link", json=body)
    assert response.status_code == 401 and response.json() == {"error": "link_invalid"}
    assert "set-cookie" not in response.headers
    assert core.store.one("SELECT 1 FROM owner_sessions") is None
    assert not core.audit.recent(action="login_link_used")


@pytest.mark.parametrize("path, failure", [("/auth/link", "link_invalid"), (SIGN_IN + "/verify", "passkey_failed")])
def test_failed_login_attempts_are_rate_limited(make, path, failure):
    _, client = make()
    for _ in range(10):
        response = client.post(path, json={})
        assert response.status_code == 401 and response.json() == {"error": failure}
    response = client.post(path, json={})
    assert response.status_code == 429 and "retry-after" in response.headers
    security_headers(response)


class Passkey:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.id = secrets.token_bytes(32)
        self.handle = None

    @staticmethod
    def data(mode, options, rp_id, flags, count):
        public_key = options["publicKey"]
        client_data = json.dumps({"type": f"webauthn.{mode}", "challenge": public_key["challenge"],
                                  "origin": PUBLIC, "crossOrigin": False}).encode()
        auth_data = hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + count.to_bytes(4, "big")
        return client_data, auth_data

    def credential(self, client_data, **response):
        return {"id": b64url(self.id), "rawId": b64url(self.id), "type": "public-key",
                "response": {"clientDataJSON": b64url(client_data), **response}}

    def registration(self, options):
        key = self.key.public_key().public_numbers()
        cose_key = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: key.x.to_bytes(32, "big"), -3: key.y.to_bytes(32, "big")})
        rp_id = options["publicKey"]["rp"]["id"]
        self.handle = options["publicKey"]["user"]["id"]
        client_data, auth_data = self.data("create", options, rp_id, 0x45, 0)
        auth_data += bytes(16) + len(self.id).to_bytes(2, "big") + self.id + cose_key
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return self.credential(client_data, attestationObject=b64url(attestation))

    def assertion(self, options, count, *, flags=0x05):
        client_data, auth_data = self.data("get", options, options["publicKey"]["rpId"], flags, count)
        sig = self.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return self.credential(client_data, authenticatorData=b64url(auth_data), signature=b64url(sig), userHandle=self.handle)


def add_options(client, headers=None):
    response = client.post(ADD + "/options", headers=headers or csrf(client))
    assert response.status_code == 200
    return response


def add_key(client, key, options=None, headers=None):
    headers = headers or csrf(client)
    options = options or add_options(client, headers).json()
    return client.post(ADD + "/verify", json={"credential": key.registration(options), "name": "Laptop"}, headers=headers)


def authenticate(client, key, count, **opts):
    options = client.post(SIGN_IN + "/options").json()
    return client.post(SIGN_IN + "/verify", json={"credential": key.assertion(options, count, **opts)})


def test_passkey_registration_discoverable_login_and_counter(make, caplog, no_spa_build):
    clock = Clock()
    core, client = make(base_config(auth={}), clock=clock)
    assert sign_in(client).status_code == 204
    key = Passkey()
    with caplog.at_level(logging.DEBUG):
        response = add_options(client)
        security_headers(response)
        cookie_attributes(response, CHALLENGE_COOKIE, 120)
        opts = response.json()
        public_key = opts["publicKey"]
        assert public_key["rp"] == {"id": urlsplit(PUBLIC).hostname, "name": "SwB"}
        assert public_key["authenticatorSelection"] == {"residentKey": "required", "requireResidentKey": True,
                                                       "userVerification": "required"}
        assert public_key["excludeCredentials"] == []
        ref = client.cookies[CHALLENGE_COOKIE]
        added = add_key(client, key, opts)
        assert added.status_code == 201 and added.json() == {"id": b64url(key.id)}
        cookie_attributes(added, CHALLENGE_COOKIE, 0)
        assert [entry["id"] for entry in client.get("/auth/passkeys").json()] == [b64url(key.id)]
        client.cookies.set(CHALLENGE_COOKIE, ref)
        replay = add_key(client, key, opts)
        assert replay.status_code == 401 and replay.json() == {"error": "passkey_failed"}
        client.cookies.clear()
        assert "Sign in with a passkey" in client.get("/login").text
        request = client.post(SIGN_IN + "/options").json()
        assert request["publicKey"]["allowCredentials"] == [] and request["publicKey"]["userVerification"] == "required"
        clock.t += 1
        assert authenticate(client, key, 7).status_code == 204
        assert client.get("/auth/session").json()["via"] == "passkey"
        assert client.get("/auth/passkeys").json() == [
            {"id": b64url(key.id), "name": "Laptop", "created_at": clock.t - 1, "last_used_at": clock.t},
        ]
        denied = authenticate(client, key, 6)
        assert denied.status_code == 401 and denied.json() == {"error": "passkey_failed"}
        assert authenticate(client, key, 7).status_code == 401
        assert authenticate(client, key, 8, flags=0x01).status_code == 401
        assert authenticate(client, key, 0).status_code == 401
        assert core.store.one("SELECT sign_count FROM owner_passkeys")["sign_count"] == 7
        assert authenticate(client, key, 8).status_code == 204
    assert all(secret not in caplog.text for secret in (ref, public_key["challenge"], request["publicKey"]["challenge"]))
    options = add_options(client).json()["publicKey"]
    assert options["user"]["id"] == key.handle
    assert options["excludeCredentials"] == [{"id": b64url(key.id), "type": "public-key"}]


def test_passkey_session_owns_api_reads_and_writes(make):
    core, client = make(base_config(auth={}))
    assert sign_in(client).status_code == 204
    key = Passkey()
    assert add_key(client, key).status_code == 201
    link_csrf = csrf(client)["X-CSRF-Token"]
    client.cookies.clear()
    assert authenticate(client, key, 1).status_code == 204
    session = client.get("/auth/session").json()
    assert session["signed_in"] and session["via"] == "passkey" and session["csrf"] != link_csrf
    read = client.get("/api/health")
    assert read.status_code == 200 and read.headers["cache-control"] == "no-store"

    path = "/api/visibility"
    rule = {"resource": "target:claude", "visibility": "shared"}
    origin = {"Origin": PUBLIC}
    for headers in (origin, {**origin, "X-CSRF-Token": "invalid"}, {**origin, "X-CSRF-Token": link_csrf},
                    {"Origin": "null", "X-CSRF-Token": session["csrf"]}):
        denied = client.post(path, json=rule, headers=headers)
        assert denied.status_code == 403 and denied.json() == {"error": "csrf"}
    assert core.visibility.explicit(rule["resource"]) is None
    assert client.post("/pairing/code", headers=origin, data={"agent": "codex", "csrf": session["csrf"]}).status_code == 200
    bad_body = client.post(path, headers=origin, data={**rule, "csrf": session["csrf"]})
    assert bad_body.status_code == 400 and bad_body.json() == {"error": "invalid_request"}
    saved = client.post(path, json=rule, headers={**origin, "X-CSRF-Token": session["csrf"]})
    assert saved.status_code == 200 and saved.json() == rule
    assert core.visibility.explicit(rule["resource"]) == "shared"
    core.store.x("INSERT INTO conversation_turns(root, turns, paused_at) VALUES (?, ?, ?)", ("h_test", 200, core.clock()))
    resumed = client.post("/api/inbox/pauses/h_test/resume", headers={**origin, "X-CSRF-Token": session["csrf"]})
    assert resumed.status_code == 200 and resumed.json() == {"root": "h_test", "resumed": True}

    client.cookies.clear()
    for denied in (client.get("/api/health"), client.post(path, headers=origin, json=rule)):
        assert denied.status_code == 401 and denied.json() == {"error": "signed_out"}


def test_passkey_challenge_bound_to_session_and_single_use_on_failure_or_expiry(make):
    clock = Clock()
    core, client = make(base_config(auth={}), clock=clock)
    assert sign_in(client).status_code == 204
    credential = Passkey()
    opts = add_options(client).json()
    assert sign_in(client).status_code == 204
    assert add_key(client, credential, opts).status_code == 401
    assert core.store.one("SELECT 1 FROM owner_webauthn_challenges") is None
    assert add_key(client, credential).status_code == 201
    client.cookies.clear()
    opts = client.post(SIGN_IN + "/options").json()
    ref = client.cookies[CHALLENGE_COOKIE]
    denied = client.post(SIGN_IN + "/verify", json={"credential": {"broken": True}})
    assert denied.status_code == 401 and denied.json() == {"error": "passkey_failed"}
    client.cookies.clear()
    client.cookies.set(CHALLENGE_COOKIE, ref)
    good = {"credential": credential.assertion(opts, 1)}
    assert client.post(SIGN_IN + "/verify", json=good).status_code == 401
    client.cookies.clear()
    opts = client.post(SIGN_IN + "/options").json()
    clock.t += 120
    assert client.post(SIGN_IN + "/verify", json={"credential": credential.assertion(opts, 1)}).status_code == 401
    assert client.get("/auth/session").json()["signed_in"] is False
    assert core.store.one("SELECT 1 FROM owner_webauthn_challenges") is None


def test_counterless_passkey_signs_in_repeatedly(make):
    _, client = make(base_config(auth={}))
    assert sign_in(client).status_code == 204
    key = Passkey()
    assert add_key(client, key).status_code == 201
    assert authenticate(client, key, 0).status_code == 204
    assert authenticate(client, key, 0).status_code == 204


def test_passkey_delete_signs_out_its_sessions(make):
    _, client = make(base_config(auth={}))
    assert sign_in(client).status_code == 204
    first, second = Passkey(), Passkey()
    for key in (first, second):
        assert add_key(client, key).status_code == 201
    assert authenticate(client, first, 1).status_code == 204
    assert client.delete("/auth/passkeys/" + b64url(first.id), headers=csrf(client)).status_code == 204
    assert client.get("/auth/session").json()["signed_in"] is False
    assert authenticate(client, second, 1).status_code == 204
    assert client.get("/auth/session").json()["via"] == "passkey"


@pytest.mark.parametrize("upstream", [None, "access", "oidc"])
def test_passkey_delete_keeps_last_method_without_upstream(make, upstream):
    config = base_config()
    if upstream != "access":
        config["auth"] = {"oidc": OIDC_CONFIG} if upstream == "oidc" else {}
    _, client = make(config, env=OIDC_ENV)
    assert sign_in(client).status_code == 204
    first, second = Passkey(), Passkey()
    for key in (first, second):
        assert add_key(client, key).status_code == 201
    path = "/auth/passkeys/" + b64url(first.id)
    assert client.delete(path).status_code == 403
    assert client.delete(path, headers=csrf(client)).status_code == 204
    response = client.delete("/auth/passkeys/" + b64url(second.id), headers=csrf(client))
    if upstream:
        assert response.status_code == 204
        assert client.get("/auth/passkeys").json() == []
    else:
        assert response.status_code == 409 and response.json() == {"error": "last_method"}
        assert len(client.get("/auth/passkeys").json()) == 1


@pytest.mark.parametrize(("method", "alg", "claim", "accepted"), [
    ("client_secret_post", "ES256", {}, True),
    ("client_secret_basic", "RS256", {"sub": "other", "email": OWNER_EMAIL, "email_verified": True}, True),
    ("client_secret_basic", "ES256", {"sub": "other", "email": OWNER_EMAIL, "email_verified": False}, False),
    ("client_secret_post", "ES256", {"nonce": "wrong"}, False),
    ("client_secret_post", "ES256", {"aud": "wrong"}, False),
])
def test_oidc_start_and_callback_with_signed_provider_token(make, method, alg, claim, accepted, no_spa_build):
    cfg = base_config(auth={"owner_emails": [OWNER_EMAIL], "oidc": OIDC_CONFIG})
    core, client = make(cfg, env=OIDC_ENV)
    identity_key = ec.generate_private_key(ec.SECP256R1()) if alg == "ES256" else rsa.generate_private_key(65537, 2048)
    algorithm = ECAlgorithm if alg == "ES256" else RSAAlgorithm
    jwk = {**json.loads(algorithm.to_jwk(identity_key.public_key())), "kid": "fixture-key", "alg": alg, "use": "sig"}
    paths = []
    params = {}

    def provider(request):
        path = request.url.path
        paths.append(path)
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
                                            "token_endpoint": ISSUER + "/token", "jwks_uri": ISSUER + "/keys",
                                            "token_endpoint_auth_methods_supported": [method],
                                            "id_token_signing_alg_values_supported": [alg]})
        if path == "/keys":
            return httpx.Response(200, json={"keys": [jwk]})
        assert path == "/token" and request.method == "POST"
        data = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        assert data["grant_type"] == "authorization_code" and data["code"] == "test-code"
        assert data["redirect_uri"] == PUBLIC + "/auth/oidc/callback"
        assert b64url(hashlib.sha256(data["code_verifier"].encode()).digest()) == params["code_challenge"][0]
        if method == "client_secret_post":
            assert data["client_id"] == OIDC_CONFIG["client_id"] and data["client_secret"] == OIDC_ENV["OIDC_SECRET"]
        else:
            auth = base64.b64decode(request.headers["authorization"].removeprefix("Basic ")).decode()
            assert auth == f'{OIDC_CONFIG["client_id"]}:{OIDC_ENV["OIDC_SECRET"]}' and "client_secret" not in data
        claims = {"iss": ISSUER, "aud": OIDC_CONFIG["client_id"], "sub": "owner-subject", "exp": int(time.time()) + 60,
                  "nonce": params["nonce"][0], **claim}
        signed = jwt.encode(claims, identity_key, algorithm=alg, headers={"kid": "fixture-key"})
        return httpx.Response(200, json={"id_token": signed})

    client.portal.call(core.auth.http.aclose)
    core.auth.http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    login = client.get("/login", params={"next": "/?approved=yes"})
    assert "Continue with Example Identity" in login.text
    assert client.get("/auth/session").json()["methods"]["oidc"] == {"name": "Example Identity"}
    start = client.get("/auth/oidc/start", params={"next": "/?approved=yes"})
    security_headers(start)
    cookie_attributes(start, OIDC_COOKIE, 300)
    target = urlsplit(start.headers["location"])
    assert target.netloc == urlsplit(ISSUER).netloc and target.path == "/authorize"
    params.update(parse_qs(target.query))
    assert params["response_type"] == ["code"] and params["code_challenge_method"] == ["S256"]
    assert params["scope"] == ["openid email"] and params["state"] != params["nonce"]
    response = client.get("/auth/oidc/callback", params={"code": "test-code", "state": params["state"][0]})
    security_headers(response)
    cookie_attributes(response, OIDC_COOKIE, 0)
    assert paths == ["/.well-known/openid-configuration", "/.well-known/openid-configuration", "/token", "/keys"]
    if accepted:
        assert response.status_code == 303 and response.headers["location"] == "/?approved=yes"
        cookie_attributes(response, SESSION_COOKIE, SESSION_LIFETIME)
        assert client.get("/auth/session").json()["via"] == "oidc"
    else:
        assert response.status_code == 303 and response.headers["location"] == "/login?error=oidc_failed"
        assert client.get("/auth/session").json()["signed_in"] is False


@pytest.mark.parametrize("changes", [{"issuer": "http://identity.example.test"}, {"client_id": ""},
                                    {"client_secret_env": "MISSING"}, {"name": None},
                                    {"owner_subjects": [""]}, {"owner_subjects": []}])
def test_oidc_configuration_errors_are_caught_when_loading(make, changes):
    with pytest.raises(ConfigError, match=r"auth\.oidc"):
        make(base_config(auth={"oidc": {**OIDC_CONFIG, **changes}}), env=OIDC_ENV)


@pytest.mark.parametrize("bad", ["//evil.example", "/\\evil.example", "https://evil.example", "relative",
                                 "/\n/evil.example", "\t//evil.example", None, ""])
def test_next_is_same_origin_only(make, bad, no_spa_build):
    assert safe_next(bad) == "/"
    _, client = make()
    response = client.get("/login", params={"next": bad or ""})
    assert response.status_code == 200 and 'data-next="/"' in response.text
    security_headers(response)


@pytest.mark.parametrize(("emails", "owner_name"), [([], "owner"), ([OWNER_EMAIL], OWNER_EMAIL)])
def test_authorize_returns_to_full_request_and_accepts_session(make, no_spa_build, emails, owner_name):
    core, client = make(base_config(auth={"owner_emails": emails}))
    cid = register(client)["client_id"]
    state = "keep the full query /+&"
    redirect, _ = consent(client, cid, headers={}, state=state)
    assert redirect.status_code == 303
    location = urlsplit(redirect.headers["location"])
    next_path = parse_qs(location.query)["next"][0]
    assert location.path == "/login" and next_path == "/authorize?" + redirect.request.url.query.decode()
    login = client.get(redirect.headers["location"])
    assert "<h1>Ask your agent for a sign-in link</h1>" in login.text
    assert 'type="password"' not in login.text and "history.replaceState" in login.text
    assert sign_in(client).status_code == 204
    consent_page = client.get(next_path)
    assert consent_page.status_code == 200
    assert f"Signed in as {owner_name}</p>" in consent_page.text
    session = core.auth.sessions.owner(client.cookies[SESSION_COOKIE])
    assert session.label not in consent_page.text and client.cookies[SESSION_COOKIE] not in consent_page.text
    req = re.search(r'name="req" value="([^"]+)"', consent_page.text).group(1)
    form = {"req": req, "decision": "approve", "csrf": consent_csrf(consent_page)}
    blocked = client.post("/authorize", data=form, headers={"Origin": "https://elsewhere.example.test"})
    assert blocked.status_code == 403 and blocked.json() == {"error": "csrf"}
    approved = client.post("/authorize", data=form, headers={"Origin": PUBLIC})
    assert approved.status_code == 302 and parse_qs(urlsplit(approved.headers["location"]).query)["state"] == [state]
