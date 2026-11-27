from urllib.parse import urlsplit

import pytest
from conftest import OWNER, PUBLIC, base_config, default_env
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from test_claims import OWNER_KEY, form_csrf, private_page

from switchboard import credentials
from switchboard.claims import ClaimError, Field, normalize, normalize_pem

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PKCS1 = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode()
PKCS8 = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


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
    title, provider, fields = credentials.github_app("GITHUB_WEBHOOK_SECRET")
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
    title, provider, fields = credentials.github_app()
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
