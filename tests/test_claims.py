import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import pytest

from conftest import OWNER, PUBLIC, STATIC_KEY, Clock, base_config, default_env, sign_in
from switchboard.__main__ import _NoQuery
from switchboard.claims import CLAIM_TTL_S, ClaimError, ClaimUnavailable, Claims
from switchboard.store import Store

NAME = "EXAMPLE_TOKEN"
VALUE = "example-credential-123"
OWNER_KEY = "test-owner-key-" + "x" * 32
ROUTINE_TOKEN = "sk-ant-oat01-" + "x" * 32


def form_csrf(page):
    return page.text.split('name="csrf" value="')[1].split('"')[0]


def private_page(page):
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "no-referrer"
    assert '<meta name="referrer" content="no-referrer">' in page.text


def test_owner_claim_stores_and_audits_without_echo(make, caplog):
    core, client = make()
    link = core.claims.mint(NAME, PUBLIC)
    path = urlsplit(link).path
    code = path.rsplit("/", 1)[-1]
    assert link.startswith(f"{PUBLIC}/claim/")
    assert core.secret(NAME) is None

    for headers in ({}, {"authorization": f"Bearer {STATIC_KEY}"}, {"cf-access-jwt-assertion": "other"}):
        assert client.get("/", headers=headers).status_code == 403
        refused = client.get(path, headers=headers)
        assert refused.status_code == 403
        private_page(refused)
        assert client.post(path, data={"token": VALUE}, headers=headers).status_code == 403

    page = client.get(path, headers=OWNER)
    assert page.status_code == 200 and NAME in page.text and 'type="password"' in page.text
    private_page(page)
    assert code not in page.text
    csrf = form_csrf(page)
    for invalid in ("", "bad", "é"):
        assert client.post(path, data={"csrf": invalid, "token": VALUE}, headers=OWNER).status_code == 403
    invalid = client.post(path, data={"csrf": csrf, "token": VALUE + "\nsecond line"}, headers=OWNER)
    assert invalid.status_code == 400 and VALUE not in invalid.text
    private_page(invalid)
    row = core.store.one("SELECT code_hash, sealed FROM secrets WHERE name = ?", (NAME,))
    assert row["code_hash"] == hashlib.sha256(code.encode()).hexdigest() and row["sealed"] is None

    with caplog.at_level(logging.INFO, logger="switchboard.audit"):
        saved = client.post(path, data={"csrf": csrf, "token": VALUE}, headers=OWNER)
    assert saved.status_code == 200 and "Run connect again" in saved.text
    private_page(saved)
    assert VALUE not in saved.text and VALUE not in caplog.text
    assert code not in saved.text and code not in caplog.text
    row = core.store.one("SELECT code_hash, sealed FROM secrets WHERE name = ?", (NAME,))
    assert row["code_hash"] is None and VALUE.encode() not in row["sealed"]
    assert core.secret(NAME) == VALUE
    spent = client.get(path, headers=OWNER)
    assert spent.status_code == 410
    private_page(spent)
    assert client.post(path, data={"csrf": csrf, "token": "replacement"}, headers=OWNER).status_code == 410
    assert core.secret(NAME) == VALUE
    [audit] = core.audit.recent(action="token_claim")
    assert audit["target"] == NAME and audit["source"] == "admin:owner@example.test" and audit["outcome"] == "ok"
    assert VALUE not in str(audit) and code not in str(audit)


def test_claims_require_access_configuration(make):
    core, client = make(config=base_config(auth={"owner_emails": ["owner@example.test"]}))
    path = urlsplit(core.claims.mint(NAME, PUBLIC)).path
    assert client.get("/", headers=OWNER).status_code == 403
    assert client.get(path, headers=OWNER).status_code == 403
    assert client.post(path, data={"token": VALUE}, headers=OWNER).status_code == 403
    assert core.secret(NAME) is None


@pytest.mark.parametrize("routine", [False, True])
@pytest.mark.parametrize("access", [False, True])
def test_owner_key_redeems_cli_claim_without_csrf(make, routine, access):
    config = base_config(version=2, blocks={"mcp": {}, "claude-routine": {}})
    if not access:
        config.pop("auth")
    core, client = make(config=config, env={**default_env(), "SWITCHBOARD_KEY_OWNER": OWNER_KEY})
    if routine:
        block = core.blocks["claude-routine"]
        core.projects.create(slug="sample", name="Sample")
        minted = block.register(fire="trig_01EXAMPLE", claude_project="Sample", project="sample", project_name=None,
                                description=None, create_new=False, name=None, primary=None, actor="test")
        path = urlsplit(minted["link"]).path
        value = ROUTINE_TOKEN
    else:
        path = urlsplit(core.claims.mint(NAME, PUBLIC)).path
        value = VALUE

    pairing = core.connections.mint(agent="example-agent", project=None, client=None, by="test")
    paired_key = core.connections.redeem(pairing["code"])["key"]
    refused = [STATIC_KEY, paired_key]
    if access:
        refused.append(sign_in(client)["access_token"])
    for key in refused:
        assert client.post(path, headers={"Authorization": f"Bearer {key}"}, data={"token": value}).status_code == 403
    headers = {"Authorization": f"Bearer {OWNER_KEY}"}
    result = client.post(path, headers=headers, data={"token": value})
    assert result.status_code == 200 and value not in result.text and OWNER_KEY not in result.text
    private_page(result)
    action = "routine_claim" if routine else "token_claim"
    [audit] = core.audit.recent(action=action)
    assert audit["source"] == "key:owner"
    spent = client.post(path, headers=headers, data={"token": value})
    assert spent.status_code == (400 if routine else 410)
    private_page(spent)


def test_no_access_form_keeps_claim_details_hidden_until_owner_auth(make):
    core, client = make(config=base_config(auth={}), env={**default_env(), "SWITCHBOARD_KEY_OWNER": OWNER_KEY})
    path = urlsplit(core.claims.mint(NAME, PUBLIC)).path
    unknown_path = "/claim/" + "f" * 43
    page = client.get(path)
    unknown = client.get(unknown_path)
    assert page.status_code == unknown.status_code == 200
    private_page(page)
    assert page.text.replace(form_csrf(page), "") == unknown.text.replace(form_csrf(unknown), "")
    assert NAME not in page.text and OWNER_KEY not in page.text
    assert page.text.count('type="password"') == 2
    assert client.get("/").status_code == 403
    for endpoint, response in ((path, page), (unknown_path, unknown)):
        assert client.post(endpoint, data={"token": VALUE, "owner_key": STATIC_KEY, "csrf": form_csrf(response)}).status_code == 403
        assert client.post(endpoint, data={"token": VALUE, "owner_key": OWNER_KEY}).status_code == 403
    assert client.post(unknown_path, data={"token": VALUE, "owner_key": OWNER_KEY, "csrf": form_csrf(page)}).status_code == 403
    saved = client.post(path, data={"token": VALUE, "owner_key": OWNER_KEY, "csrf": form_csrf(page)})
    assert saved.status_code == 200 and VALUE not in saved.text and OWNER_KEY not in saved.text
    assert client.get(path).text == page.text
    assert core.secret(NAME) == VALUE


@pytest.mark.parametrize("restriction", [{"key_scopes": {"owner": ["general"]}}, {"inbox_keys": {"owner": "example-agent"}}])
def test_designated_key_must_be_unrestricted_for_claim(make, restriction):
    core, client = make(config=base_config(auth=restriction), env={**default_env(), "SWITCHBOARD_KEY_OWNER": OWNER_KEY})
    path = urlsplit(core.claims.mint(NAME, PUBLIC)).path
    assert client.get(path).status_code == 403
    assert client.post(path, headers={"Authorization": f"Bearer {OWNER_KEY}"}, data={"token": VALUE}).status_code == 403
    assert core.secret(NAME) is None


def test_claim_expiry_and_newest_link_wins_per_name(make):
    clock = Clock()
    core, client = make(clock=clock)
    first = urlsplit(core.claims.mint(NAME, PUBLIC)).path
    other = urlsplit(core.claims.mint("OTHER_TOKEN", PUBLIC)).path
    assert client.get(first, headers=OWNER).status_code == 200
    clock.t += CLAIM_TTL_S - 1
    newest = urlsplit(core.claims.mint(NAME, PUBLIC)).path
    assert newest != first
    assert client.get(first, headers=OWNER).status_code == 410
    assert client.get(other, headers=OWNER).status_code == 200
    clock.t += 1
    assert client.get(other, headers=OWNER).status_code == 410
    page = client.get(newest, headers=OWNER)
    assert page.status_code == 200
    csrf = form_csrf(page)
    clock.t += CLAIM_TTL_S - 1
    assert client.post(newest, data={"csrf": csrf, "token": VALUE}, headers=OWNER).status_code == 410
    assert core.secret(NAME) is None


def test_claim_consumption_is_atomic_and_persisted(make):
    core, _ = make(env={**default_env(), NAME: "environment-value"})
    second_store = Store(core.store.path, clock=core.clock)
    try:
        second = Claims(second_store)
        code = core.claims.mint(NAME, PUBLIC).rsplit("/", 1)[-1]

        def consume(claim):
            try:
                return claim.consume(code, VALUE)
            except ClaimUnavailable:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(consume, [core.claims, second])).count(NAME) == 1
    finally:
        second_store.close()
    new_store = Store(core.store.path)
    try:
        assert Claims(new_store).secret(NAME) == VALUE
        assert core.secret(NAME) == "environment-value"
        assert (core.data_dir / "claim-key").stat().st_mode & 0o777 == 0o600
    finally:
        new_store.close()


def test_invalid_claim_requests_fail_without_registering_a_secret(make):
    core, _ = make()
    with pytest.raises(ClaimError, match="environment variable"):
        core.claims.mint("not-a-name", PUBLIC)
    with pytest.raises(ClaimError, match="public_url"):
        core.claims.mint(NAME, None)
    assert core.store.q("SELECT name FROM secrets") == []


@pytest.mark.parametrize("prefix", ["/claim/", "/routines/claim/"])
def test_uvicorn_access_log_does_not_show_claim_code(prefix):
    secret_code = "claim-code"
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s %s %s %s %s",
                               ("127.0.0.1", "GET", f"{prefix}{secret_code}?state=secret-state", "1.1", 200), None)
    assert _NoQuery().filter(record)
    assert secret_code not in record.getMessage() and "secret-state" not in record.getMessage()
    assert f"{prefix}[redacted]" in record.getMessage()
