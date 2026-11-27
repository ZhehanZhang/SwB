import base64
from urllib.parse import urlsplit

import httpx
import pytest
from conftest import OWNER, PUBLIC, base_config, default_env
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from test_claims import OWNER_KEY, form_csrf, private_page

from switchboard import credentials
from switchboard.auth import Principal
from switchboard.blocks.github.api import github_app
from switchboard.blocks.slack.credentials import check_slack_auth, slack_workspace
from switchboard.claims import ClaimError, Claims, Field, normalize, normalize_pem

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PKCS1 = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode()
PKCS8 = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def test_single_github_claims_validate_before_saving(make):
    core, client = make(config=base_config(version=2, blocks={"mcp": {}, "github": {}}))
    block = core.blocks["github"]
    connect = client.portal.call(block.connect_steps, "github", Principal("owner", "test", "key"), None, {"credential": "app"})
    paths = {core.claims.inspect(urlsplit(step["link"]).path.rsplit("/", 1)[1]): urlsplit(step["link"]).path
             for step in connect["next"] if "link" in step}
    examples = {
        "GITHUB_APP_ID": ("app-id-x", "001", "12345", "Enter a numeric ID"),
        "GITHUB_WEBHOOK_SECRET": ("short", "x" * 15, "x" * 16, "Use a webhook secret with at least 16 characters"),
        "GITHUB_APP_PRIVATE_KEY": ("not a pem", "-----BEGIN PRIVATE KEY-----YWJj-----END PRIVATE KEY-----",
                                   PKCS8, "Paste the full private key or upload the .pem file"),
    }
    assert set(paths) == set(examples)
    for name, (bad, also_bad, good, error) in examples.items():
        path = paths[name]
        csrf = form_csrf(client.get(path, headers=OWNER))
        invalid = client.post(path, headers=OWNER, data={"csrf": csrf, "token": bad})
        assert invalid.status_code == 400 and error in invalid.text and bad not in invalid.text
        assert client.post(path, headers=OWNER, data={"csrf": csrf, "token": also_bad}).status_code == 400
        assert core.claims.secret(name) is None and client.get(path, headers=OWNER).status_code == 200
        assert client.post(path, headers=OWNER, data={"csrf": csrf, "token": good}).status_code == 200
        assert core.claims.secret(name) == good
        assert client.get(path, headers=OWNER).status_code == 410


def test_reissued_github_claim_and_earlier_link_are_each_single_use(make):
    core, client = make(config=base_config(version=2, blocks={"mcp": {}, "github": {}}))
    block = core.blocks["github"]

    def app_id_link():
        result = client.portal.call(block.connect_steps, "github", Principal("owner", "test", "key"), None, {"credential": "app"})
        return next(urlsplit(step["link"]).path for step in result["next"] if "link" in step
                    and core.claims.inspect(step["link"].rsplit("/", 1)[1]) == "GITHUB_APP_ID")

    earlier, latest = app_id_link(), app_id_link()
    assert earlier != latest
    assert client.get(earlier, headers=OWNER).status_code == 200
    for path, value in ((latest, "12345"), (earlier, "23456")):
        csrf = form_csrf(client.get(path, headers=OWNER))
        assert client.post(path, headers=OWNER, data={"csrf": csrf, "token": value}).status_code == 200
        assert client.get(path, headers=OWNER).status_code == 410
        assert core.claims.secret("GITHUB_APP_ID") == value


def test_github_single_claim_forms_and_inventory_show_the_credential_names(make):
    config = base_config(version=2, blocks={"mcp": {}, "github": {"private_key_env": "CUSTOM_APP_KEY"}})
    core, client = make(config=config)
    block = core.blocks["github"]
    connect = client.portal.call(block.connect_steps, "github", Principal("owner", "test", "key"), None, {"credential": "app"})
    fields = {"GITHUB_APP_ID": ("App ID", 'inputmode="numeric"'),
              "GITHUB_WEBHOOK_SECRET": ("Webhook secret", 'name="token" type="password"'),
              "CUSTOM_APP_KEY": ("Private key", '<textarea id="f-token" name="token"')}
    for step in connect["next"]:
        if "link" not in step:
            continue
        path = urlsplit(step["link"]).path
        assert client.get(path).status_code == 303
        name = core.claims.inspect(path.rsplit("/", 1)[1])
        label, control = fields[name]
        page = client.get(path, headers=OWNER)
        private_page(page)
        assert f"<h1>{label}</h1>" in page.text and f">{label}</label>" in page.text
        assert control in page.text and "GitHub credential" not in page.text
        if name == "CUSTOM_APP_KEY":
            assert 'type="file" accept=".pem,.key,.txt" data-target="f-token"' in page.text
            assert '<script src="/claim-assets/claim.js" defer></script>' in page.text
            assert "type=\"password\"" not in page.text
            saved = client.post(path, headers=OWNER, data={"csrf": form_csrf(page), "token": PKCS8})
            assert saved.status_code == 200 and "<h1>Private key saved</h1>" in saved.text

    inventory = client.get("/api/tokens", headers=OWNER).json()
    assert {(r["name"], r["kind"]) for r in inventory["waiting"]} == {
        ("GitHub App ID", "github"), ("GitHub webhook secret", "github"),
    }
    assert [(r["name"], r["kind"]) for r in inventory["stored"]] == [("GitHub private key", "github")]


def test_typed_private_key_form_can_be_used_with_an_owner_key(make):
    config = base_config(auth={})
    config.pop("public_url")
    core, client = make(config=config, env={**default_env(), "SWITCHBOARD_KEY_OWNER": OWNER_KEY})
    path = urlsplit(core.claims.mint("EXAMPLE_KEY", PUBLIC, kind="pem", label="Private key")).path
    page = client.get(path)
    assert page.status_code == 200 and 'name="owner_key"' in page.text and '<textarea id="f-token"' in page.text
    assert client.post(path, data={"csrf": form_csrf(page), "token": PKCS8}).status_code == 403
    saved = client.post(path, data={"owner_key": OWNER_KEY, "csrf": form_csrf(page), "token": PKCS8})
    assert saved.status_code == 200 and core.claims.secret("EXAMPLE_KEY") == PKCS8


@pytest.mark.parametrize("legacy", ["not a pem", base64.b64encode(b"-----BEGIN PRIVATE KEY-----YWJj-----END PRIVATE KEY-----").decode()])
def test_github_connect_reissues_claim_for_unloadable_stored_private_key(make, legacy):
    config = base_config(version=2, blocks={"mcp": {}, "github": {"app_id": 12345, "private_key_env": "CUSTOM_APP_KEY"}})
    core, client = make(config=config)
    block = core.blocks["github"]
    legacy_claims = Claims(core.store)
    old_claim = legacy_claims.mint("CUSTOM_APP_KEY", PUBLIC).rsplit("/", 1)[1]
    legacy_claims.consume(old_claim, legacy)
    assert core.claims.secret("CUSTOM_APP_KEY") == legacy

    def steps():
        return client.portal.call(block.connect_steps, "github", Principal("owner", "test", "key"), None, None)["next"]

    path, = [urlsplit(step["link"]).path for step in steps() if "link" in step
             and core.claims.inspect(step["link"].rsplit("/", 1)[1]) == "CUSTOM_APP_KEY"]
    assert path != f"/claim/{old_claim}"
    assert core.claims.inspect_details(path.rsplit("/", 1)[1])["kind"] == "pem"
    csrf = form_csrf(client.get(path, headers=OWNER))
    key = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()
    assert client.post(path, headers=OWNER, data={"csrf": csrf, "token": key}).status_code == 400
    assert core.claims.secret("CUSTOM_APP_KEY") == legacy
    assert client.post(path, headers=OWNER, data={"csrf": csrf, "token": PKCS1}).status_code == 200
    assert block.api.app_jwt().count(".") == 2
    assert all("link" not in step or core.claims.inspect(step["link"].rsplit("/", 1)[1]) != "CUSTOM_APP_KEY" for step in steps())


@pytest.mark.parametrize("rotation", ["", "xoxe."])
def test_slack_group_checks_prefixes_and_retries_invalid_auth_atomically(make, monkeypatch, rotation):
    core, client = make(config=base_config(version=2, blocks={"slack": {"engine": False}}))
    env = {"bot": "CUSTOM_BOT", "user": "CUSTOM_USER", "app": "CUSTOM_APP"}
    values = {"CUSTOM_BOT": rotation + "xoxb-example", "CUSTOM_USER": rotation + "xoxp-example", "CUSTOM_APP": "xapp-example"}
    title, provider, fields = slack_workspace("Example", env)
    path = urlsplit(core.claims.mint_group(title, fields, PUBLIC, provider=provider)).path
    csrf = form_csrf(client.get(path, headers=OWNER))
    checks = []
    accepted = False

    async def verify(kind, token):
        checks.append((kind, token))
        return accepted if kind == "slack_user" else None

    monkeypatch.setattr(core.blocks["slack"], "claim_auth_check", verify)
    for name, bad, prefix in zip(values, ("example-bad-bot", "example-bad-user", "example-bad-app"), ("xoxb-", "xoxp-", "xapp-")):
        response = client.post(path, headers=OWNER, data={"csrf": csrf, **values, name: bad})
        assert response.status_code == 400 and bad not in response.text
        assert form_csrf(response) == csrf and f'id="f-{name}-error" role="alert"' in response.text
        assert f"Use a Slack token that starts with {prefix}" in response.text
        assert all(value not in response.text for value in values.values())
    assert not checks

    rejected = client.post(path, headers=OWNER, data={"csrf": csrf, **values})
    assert rejected.status_code == 400 and "Check User token" in rejected.text
    assert "Use a token accepted by Slack" in rejected.text and form_csrf(rejected) == csrf
    assert set(checks) == {(f"slack_{kind}", values[name]) for kind, name in env.items()}
    assert all(core.secret(name) is None for name in values)
    assert client.get(path, headers=OWNER).status_code == 200

    accepted = True
    saved = client.post(path, headers=OWNER, data={"csrf": csrf, **values})
    assert saved.status_code == 200 and "3 credentials saved" in saved.text
    assert {name: core.secret(name) for name in values} == values
    assert client.get(path, headers=OWNER).status_code == 410
    assert client.post(path, headers=OWNER, data=values).status_code == 410


@pytest.mark.parametrize(("kind", "method", "status", "body", "expected"), [
    ("slack_bot", "auth.test", 200, {"ok": True}, True),
    ("slack_user", "auth.test", 200, {"ok": False, "error": "invalid_auth"}, False),
    ("slack_app", "apps.connections.open", 200, {"ok": False, "error": "missing_scope"}, False),
    ("slack_bot", "auth.test", 429, {"ok": False, "error": "ratelimited"}, None),
    ("slack_bot", "auth.test", 503, {"ok": False}, None),
    ("slack_bot", "auth.test", None, None, None),
])
async def test_slack_auth_checks_use_the_right_api_and_tolerate_offline(monkeypatch, kind, method, status, body, expected):
    def slack(request):
        assert request.method == "POST" and str(request.url) == f"https://slack.com/api/{method}"
        assert request.headers["Authorization"] == "Bearer example-token"
        if status is None:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(status, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(slack))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: client)
    assert await check_slack_auth(kind, "example-token") is expected


@pytest.mark.parametrize("pem", [PKCS1, PKCS8, PKCS1.replace("\n", ""), PKCS8.replace("\n", " "), "\n\n" + PKCS1 + "\n"])
def test_pem_survives_a_one_line_paste(pem):
    out = normalize_pem(pem)
    assert out.splitlines()[0].startswith("-----BEGIN ") and all(len(line) <= 64 for line in out.splitlines())
    serialization.load_pem_private_key(out.encode(), password=None)


@pytest.mark.parametrize(("kind", "value"), [("id", "12a"), ("id", ""), ("pem", "not a key"), ("pem", PKCS1.replace("MII", "MIX", 1)),
                                             ("json", "[1]"), ("json", "{"), ("token", "a\nb"), ("url", "http://example.test"),
                                             ("pem", "-----BEGIN PRIVATE KEY-----" + "A" * 70000 + "-----END PRIVATE KEY-----")])
def test_each_kind_is_checked(kind, value):
    with pytest.raises(ClaimError):
        normalize(kind, value)


def test_group_saves_every_field_together_or_nothing(make, caplog):
    core, client = make()
    title, provider, fields = github_app("GITHUB_WEBHOOK_SECRET")
    path = urlsplit(core.claims.mint_group(title, fields, PUBLIC, provider=provider)).path
    page = client.get(path, headers=OWNER)
    assert page.status_code == 200 and 'data-connector="github"' in page.text and "For GitHub App." in page.text
    assert "https://github.com/settings/apps" in page.text
    assert 'type="file"' in page.text and 'accept=".pem,.key,.txt"' in page.text and "Webhook secret" in page.text
    private_page(page)
    csrf = form_csrf(page)
    bad = client.post(path, data={"csrf": csrf, "GITHUB_APP_ID": "abc", "GITHUB_APP_PRIVATE_KEY": PKCS1}, headers=OWNER)
    assert bad.status_code == 400 and "App ID" in bad.text and PKCS1[40:80] not in bad.text
    assert core.secret("GITHUB_APP_ID") is None and core.secret("GITHUB_APP_PRIVATE_KEY") is None
    missing = client.post(path, data={"csrf": csrf, "GITHUB_APP_ID": "12345"}, headers=OWNER)
    assert missing.status_code == 400 and core.secret("GITHUB_APP_ID") is None
    ok = client.post(path, data={"csrf": csrf, "GITHUB_APP_ID": " 12345 ", "GITHUB_APP_PRIVATE_KEY": PKCS1.replace("\n", "")},
                     headers={**OWNER, "Origin": PUBLIC})
    assert ok.status_code == 200 and "2 credentials saved" in ok.text
    assert core.secret("GITHUB_APP_ID") == "12345" and core.secret("GITHUB_APP_PRIVATE_KEY") == normalize_pem(PKCS1)
    assert core.secret("GITHUB_WEBHOOK_SECRET") is None
    assert client.post(path, data={"csrf": csrf, "GITHUB_APP_ID": "1", "GITHUB_APP_PRIVATE_KEY": PKCS1}, headers=OWNER).status_code == 410
    assert core.secret("GITHUB_APP_ID") == "12345"
    assert PKCS1[40:80] not in caplog.text and "12345" not in str(core.store.q("SELECT * FROM audit"))


def test_group_claims_need_the_owner(make):
    core, client = make()
    path = urlsplit(core.claims.mint_group("Example", [Field("EXAMPLE_ID", "ID", "id")], PUBLIC)).path
    page = client.get(path)
    assert page.status_code == 303 and page.headers["location"].startswith("/login?next=%2Fclaim%2F")
    assert page.headers["cache-control"] == "no-store" and page.headers["referrer-policy"] == "no-referrer"
    assert "EXAMPLE_ID" not in page.text
    denied = client.post(path, data={"EXAMPLE_ID": "1"})
    assert denied.status_code == 401 and denied.json() == {"error": "signed_out"}
    assert core.secret("EXAMPLE_ID") is None


def test_group_claims_refuse_duplicate_or_bad_names(make):
    core, _ = make()
    with pytest.raises(ClaimError):
        core.claims.mint_group("x", [Field("A", "a"), Field("A", "b")], PUBLIC)
    with pytest.raises(ClaimError):
        core.claims.mint_group("x", [Field("1bad", "a")], PUBLIC)
    with pytest.raises(ClaimError):
        core.claims.mint_group("x", [Field("OK", "a", "weird")], PUBLIC)


@pytest.mark.parametrize("name", ["claim.css", "claim.js", "github.svg", "slack.svg", "discord.svg", "telegram.svg", "chatgpt.svg", "swb.svg"])
def test_claim_assets_are_served(make, name):
    _, client = make()
    r = client.get(f"/claim-assets/{name}")
    assert r.status_code == 200 and r.content
    assert client.get("/claim-assets/../secret").status_code == 404


def test_every_provider_mark_is_bundled():
    for p in credentials.PROVIDERS.values():
        mark = credentials.mark(p.key)
        assert f'data-connector="{p.key}"' in mark
        if avatar := credentials.agent_avatar(p.key):
            assert (credentials.BUNDLED_AVATARS / avatar).is_file() and f'src="/avatars/{avatar}"' in mark
        else:
            assert (credentials.MARKS / f"{p.mark}.svg").is_file() and f"class=\"mark mark-{p.key}\"" in mark


def test_owner_key_installs_get_the_grouped_form(make):
    config = base_config(auth={})
    config.pop("public_url")
    core, client = make(config=config, env={**default_env(), "SWITCHBOARD_KEY_OWNER": OWNER_KEY})
    title, provider, fields = github_app()
    path = urlsplit(core.claims.mint_group(title, fields, PUBLIC, provider=provider)).path
    page = client.get(path)
    assert page.status_code == 200 and 'name="owner_key"' in page.text
    assert 'name="GITHUB_APP_ID"' in page.text and 'name="GITHUB_APP_PRIVATE_KEY"' in page.text and 'name="token"' not in page.text
    values = {"owner_key": OWNER_KEY, "GITHUB_APP_ID": "12345", "GITHUB_APP_PRIVATE_KEY": PKCS8}
    private_page(page)
    assert client.post(path, data=values).status_code == 403
    values["csrf"] = form_csrf(page)
    assert client.post(path, headers={"Origin": "https://other.example.test"}, data=values).status_code == 403
    assert core.secret("GITHUB_APP_ID") is None
    saved = client.post(path, data=values)
    assert saved.status_code == 200 and core.secret("GITHUB_APP_ID") == "12345"
