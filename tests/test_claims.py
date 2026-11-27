import hashlib
import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import pytest

from conftest import OWNER, PUBLIC, STATIC_KEY, Clock, base_config, default_env, sign_in
from switchboard.__main__ import _NoQuery
from switchboard.claims import CLAIM_TTL_S, ClaimError, ClaimUnavailable, Claims, Field
from switchboard.store import Store

NAME = "EXAMPLE_TOKEN"
VALUE = "example-credential-123"
OWNER_KEY = "test-owner-key-" + "x" * 32
ROUTINE_TOKEN = "sk-ant-oat01-" + "x" * 32


def form_csrf(page):
    return page.text.split('name="csrf" value="')[1].split('"')[0]


def private_page(page):
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "same-origin"
    assert '<meta name="referrer" content="same-origin">' in page.text


def assert_claim_error_title(response, title, status=400):
    assert response.status_code == status
    rendered = html.unescape(re.search(r"<h1\b[^>]*>([^<]+)</h1>", response.text)[1])
    assert not re.search(r"[:;—]|\b(?:no|not|never|without)\b|n['’]t", rendered, re.I)
    assert rendered == title


def test_credential_claim_error_titles_use_positive_copy(make, monkeypatch):
    core, client = make(base_config(version=2, blocks={
        "github": {}, "muse": {}, "slack": {"engine": False},
        "discord": {"connect": False, "token_env": "COPY_DISCORD_TOKEN"},
    }))
    pem = "-----BEGIN PRIVATE KEY-----" + "YWJj" + "-----END PRIVATE KEY-----"
    cases = [
        ("COPY_TOKEN", "token", "", "Enter a single-line value up to 8 KiB"),
        ("COPY_URL", "url", "http://example.test", "Enter a public HTTPS webhook URL"),
        ("COPY_ID", "id", "abc", "Enter a numeric ID"),
        ("GITHUB_APP_ID", "id", "0", "Enter a numeric App ID"),
        ("GITHUB_WEBHOOK_SECRET", "webhook_secret", "short", "Use a webhook secret with at least 16 characters"),
        ("COPY_PEM", "pem", "x" * (64 * 1024 + 1), "Use a text file up to 64 KiB"),
        ("COPY_PEM", "pem", "bad key", "Paste the full private key or upload the .pem file"),
        ("COPY_PEM", "pem", pem, "Use a valid .pem file"),
        ("GITHUB_APP_PRIVATE_KEY", "pem", pem, "Use the .pem file that GitHub downloads."),
        ("COPY_JSON", "json", "[]", "Use a JSON object"),
        ("COPY_SLACK_BOT", "slack_bot", "abc", "Use a Slack token that starts with xoxb-"),
        ("COPY_SLACK_USER", "slack_user", "abc", "Use a Slack token that starts with xoxp-"),
        ("COPY_SLACK_APP", "slack_app", "abc", "Use a Slack token that starts with xapp-"),
        ("COPY_SLACK_BOT", "slack_bot", "xoxb-example", "Use a token accepted by Slack"),
        ("COPY_DISCORD_TOKEN", "token", "wrong", "Use a bot token"),
        ("MUSE_BRIDGE_TOKEN", "token", "bad", "Paste the bridge token from Muse."),
        ("COPY_INTERNAL_ERROR", "token", "test", "Check the value"),
    ]

    async def reject_slack(kind, token):
        return False

    def internal_error(value):
        raise ClaimError("Internal error: use another link")

    monkeypatch.setattr(core.blocks["slack"], "claim_auth_check", reject_slack)
    core.claims.register_name("COPY_INTERNAL_ERROR", internal_error)
    for name, kind, value, title in cases:
        path = urlsplit(core.claims.mint(name, PUBLIC, kind=kind)).path
        csrf = form_csrf(client.get(path, headers=OWNER))
        assert_claim_error_title(client.post(path, headers=OWNER, data={"csrf": csrf, "token": value}), title)

    assert_claim_error_title(client.post(path, headers={"Authorization": "Bearer example"}), "Use the SwB owner key", 403)
    assert_claim_error_title(client.get("/claim/unknown", headers=OWNER), "Request a new link", 410)
    for label in ("App ID", "Webhook secret", "Private key", "Bot token", "User token", "App-level token"):
        path = urlsplit(core.claims.mint_group("Example group", [Field("COPY_FIELD", label)], PUBLIC)).path
        csrf = form_csrf(client.get(path, headers=OWNER))
        assert_claim_error_title(client.post(path, headers=OWNER, data={"csrf": csrf}), f"Check {label}")
    assert_claim_error_title(client.get("/routines/claim/unknown", headers=OWNER), "Claude routine setup required")


def test_routine_and_chat_claim_error_titles_use_positive_copy(make):
    clock = Clock()
    core, client = make(base_config(version=2, blocks={"claude-routine": {}, "discord": {"connect": False}}), clock=clock)
    core.projects.create(slug="sample", name="Sample")
    routine = core.blocks["claude-routine"]

    def routine_path():
        registration = routine.register(fire="trig_01EXAMPLE", claude_project="Sample", project="sample", project_name=None,
                                        description=None, create_new=False, name=None, primary=None, actor="test")
        return urlsplit(registration["link"]).path

    first, current = routine_path(), routine_path()
    assert_claim_error_title(client.get(first, headers=OWNER), "Use the latest link", 410)
    csrf = form_csrf(client.get(current, headers=OWNER))
    assert_claim_error_title(client.post(current, headers=OWNER, data={"csrf": csrf, "token": "bad"}), "Use a Claude routine token")
    assert client.post(current, headers=OWNER, data={"csrf": csrf, "token": ROUTINE_TOKEN}).status_code == 200
    assert_claim_error_title(client.get(current, headers=OWNER), "Link already used", 410)
    expired_routine = routine_path()
    discord = core.blocks["discord"]
    expired_bot = urlsplit(discord.claims.open("bot_token", "test")["link"]).path
    csrf = form_csrf(client.get(expired_bot, headers=OWNER))
    assert_claim_error_title(client.post(expired_bot, headers=OWNER, data={"csrf": csrf, "token": "bad"}), "Use a bot token")
    clock.t += CLAIM_TTL_S
    for path in (expired_routine, expired_bot):
        assert_claim_error_title(client.get(path, headers=OWNER), "Link expired", 410)
    assert_claim_error_title(client.get("/claims/discord/unknown", headers=OWNER), "Request a new link", 410)


def test_webhook_url_claim_rejects_invalid_urls_without_spending_link(make):
    core, client = make()
    path = urlsplit(core.claims.mint("EXAMPLE_WEBHOOK_URL", PUBLIC, url=True)).path
    csrf = form_csrf(client.get(path, headers=OWNER))
    invalid = ("http://webhook.example.test/private", "https://user@webhook.example.test/private",
               "https://webhook.example.test/private#fragment", "https://webhook.example.test/private\nsecond line",
               "https://127.0.0.1/private", "https://2130706433/private", "https://[::1]/private",
               "https://[::ffff:127.0.0.1]/private", "https://worker.localhost/path")
    for value in invalid:
        response = client.post(path, data={"token": value, "csrf": csrf}, headers=OWNER)
        assert response.status_code == 400 and value not in response.text
        assert core.secret("EXAMPLE_WEBHOOK_URL") is None
    valid = "https://webhook.example.test/private?key=example"
    response = client.post(path, data={"token": valid, "csrf": csrf}, headers=OWNER)
    assert response.status_code == 200 and valid not in response.text
    assert core.secret("EXAMPLE_WEBHOOK_URL") == valid


def test_owner_claim_stores_and_audits_without_echo(make, caplog):
    core, client = make()
    link = core.claims.mint(NAME, PUBLIC)
    path = urlsplit(link).path
    code = path.rsplit("/", 1)[-1]
    assert link.startswith(f"{PUBLIC}/claim/")
    assert core.secret(NAME) is None

    for headers in ({}, {"authorization": f"Bearer {STATIC_KEY}"}, {"cf-access-jwt-assertion": "other"}):
        root = client.get("/", headers=headers)
        assert root.status_code == 303 and root.headers["location"] == "/login?next=%2F"
        refused = client.get(path, headers=headers)
        assert refused.status_code == 303 and refused.headers["location"].startswith("/login?next=%2Fclaim%2F")
        assert refused.headers["cache-control"] == "no-store" and refused.headers["referrer-policy"] == "no-referrer"
        posted = client.post(path, data={"token": VALUE}, headers=headers)
        if "authorization" in headers:
            assert posted.status_code == 403
        else:
            assert posted.status_code == 401 and posted.json() == {"error": "signed_out"}

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
    row = core.store.one("SELECT code_hash FROM claim_links WHERE name = ?", (NAME,))
    assert row["code_hash"] == hashlib.sha256(code.encode()).hexdigest() and core.claims.secret(NAME) is None

    with caplog.at_level(logging.INFO, logger="switchboard.audit"):
        saved = client.post(path, data={"csrf": csrf, "token": VALUE}, headers=OWNER)
    assert saved.status_code == 200 and "<h1>Token saved</h1>" in saved.text
    private_page(saved)
    assert VALUE not in saved.text and VALUE not in caplog.text
    assert code not in saved.text and code not in caplog.text
    row = core.store.one("SELECT sealed FROM secrets WHERE name = ?", (NAME,))
    assert not core.claims.pending(NAME) and VALUE.encode() not in row["sealed"]
    assert core.secret(NAME) == VALUE
    spent = client.get(path, headers=OWNER)
    assert spent.status_code == 410
    private_page(spent)
    assert client.post(path, data={"csrf": csrf, "token": "replacement"}, headers=OWNER).status_code == 410
    assert core.secret(NAME) == VALUE
    [audit] = core.audit.recent(action="token_claim")
    assert audit["target"] == NAME and audit["source"] == "admin:owner@example.test" and audit["outcome"] == "ok"
    assert VALUE not in str(audit) and code not in str(audit)


def test_claim_does_not_accept_access_assertions_without_access_configured(make):
    core, client = make(config=base_config(auth={"owner_emails": ["owner@example.test"]}))
    path = urlsplit(core.claims.mint(NAME, PUBLIC)).path
    assert client.get("/", headers=OWNER).headers["location"] == "/login?next=%2F"
    assert client.get(path, headers=OWNER).headers["location"].startswith("/login?next=%2Fclaim%2F")
    posted = client.post(path, data={"token": VALUE}, headers=OWNER)
    assert posted.status_code == 401 and posted.json() == {"error": "signed_out"}
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


def test_no_owner_sign_in_form_keeps_claim_details_hidden_until_owner_auth(make):
    config = base_config(auth={})
    config.pop("public_url")
    core, client = make(config=config, env={**default_env(), "SWITCHBOARD_KEY_OWNER": OWNER_KEY})
    path = urlsplit(core.claims.mint(NAME, PUBLIC)).path
    unknown_path = "/claim/" + "f" * 43
    page = client.get(path)
    unknown = client.get(unknown_path)
    assert page.status_code == unknown.status_code == 200
    private_page(page)
    assert page.text.replace(form_csrf(page), "") == unknown.text.replace(form_csrf(unknown), "")
    assert NAME not in page.text and OWNER_KEY not in page.text
    assert page.text.count('type="password"') == 2
    assert client.get("/").headers["location"] == "/login?next=%2F"
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
    page = client.get(path)
    assert page.status_code == 303 and page.headers["location"].startswith("/login?next=%2Fclaim%2F")
    assert client.post(path, headers={"Authorization": f"Bearer {OWNER_KEY}"}, data={"token": VALUE}).status_code == 403
    assert core.secret(NAME) is None


def test_each_claim_link_has_its_own_expiry(make):
    clock = Clock()
    core, client = make(clock=clock)
    first = urlsplit(core.claims.mint(NAME, PUBLIC)).path
    other = urlsplit(core.claims.mint("OTHER_TOKEN", PUBLIC)).path
    assert client.get(first, headers=OWNER).status_code == 200
    clock.t += CLAIM_TTL_S - 1
    newest = urlsplit(core.claims.mint(NAME, PUBLIC)).path
    assert newest != first
    assert client.get(first, headers=OWNER).status_code == 200
    assert client.get(other, headers=OWNER).status_code == 200
    clock.t += 1
    assert client.get(first, headers=OWNER).status_code == 410
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
    with pytest.raises(ClaimError, match="credential kind"):
        core.claims.mint(NAME, PUBLIC, kind="unknown")
    assert core.store.q("SELECT name FROM secrets") == []


def test_existing_claim_tables_gain_kind_and_label_without_losing_links(tmp_path):
    store = Store(tmp_path / "legacy.db", clock=Clock())
    try:
        store.x("CREATE TABLE secrets (name TEXT PRIMARY KEY, code_hash TEXT UNIQUE, expires_at REAL, sealed BLOB, require_https INTEGER)")
        code = "f" * 43
        store.x("INSERT INTO secrets VALUES (?, ?, ?, NULL, 1)", (NAME, hashlib.sha256(code.encode()).hexdigest(), store.clock() + 100))
        claims = Claims(store)
        assert claims.inspect_details(code) == {
            "name": NAME, "kind": "token", "label": "", "expires_at": store.clock() + 100, "require_https": 1,
        }
        with pytest.raises(ClaimError):
            claims.consume(code, VALUE)
        assert claims.consume(code, "https://hook.example.test") == NAME
        assert claims.secret(NAME) == "https://hook.example.test"
    finally:
        store.close()


@pytest.mark.parametrize("prefix", ["/claim/", "/routines/claim/"])
def test_uvicorn_access_log_does_not_show_claim_code(prefix):
    secret_code = "claim-code"
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s %s %s %s %s",
                               ("127.0.0.1", "GET", f"{prefix}{secret_code}?state=secret-state", "1.1", 200), None)
    assert _NoQuery().filter(record)
    assert secret_code not in record.getMessage() and "secret-state" not in record.getMessage()
    assert f"{prefix}[redacted]" in record.getMessage()
