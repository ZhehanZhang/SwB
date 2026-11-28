from importlib.metadata import PackageNotFoundError, version
from subprocess import CompletedProcess, TimeoutExpired

import pytest

from conftest import OWNER, PUBLIC, STATIC_KEY, base_config, default_env
import switchboard
from switchboard import api_about
from switchboard.blocks import Block, Manifest


class ExampleBlock(Block):
    manifest = Manifest(id="example", title="Example")


def test_about_is_owner_only_and_lists_enabled_blocks(make, monkeypatch):
    sha = "a1b2c3d" * 5 + "e1f23"
    date = "2026-11-26T23:24:00Z"
    _, client = make(base_config(version=2, blocks={
        "client": {"use": "mcp"}, "slack": {"enabled": False},
    }), env={**default_env(), "GIT_COMMIT": sha.upper(), "BUILD_DATE": date})
    monkeypatch.setattr(api_about.subprocess, "run", lambda *args, **kwargs: pytest.fail("must use baked commit"))

    for headers in ({}, {"Authorization": f"Bearer {STATIC_KEY}"}, {"cf-access-jwt-assertion": "other"}):
        response = client.get("/api/about", headers=headers)
        assert response.status_code == 401 and response.json() == {"error": "signed_out"}
        assert response.headers["cache-control"] == "no-store"

    response = client.get("/api/about", headers=OWNER)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "version": version("swb-one"),
        "commit": sha,
        "build_date": date,
        "blocks": [{"name": "client", "id": "mcp", "version": version("swb-one")},
                   {"name": "webhook", "id": "webhook", "version": version("swb-one")}],
    }


def test_about_reports_the_installed_plugin_distribution_version(make, monkeypatch, tmp_path):
    dist = tmp_path / "swb_example_plugin-1.2.3.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: swb-example-plugin\nVersion: 1.2.3\n")
    (dist / "entry_points.txt").write_text(f"[switchboard.blocks]\nplugin = {ExampleBlock.__module__}:ExampleBlock\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(api_about, "_REPO_ROOT", tmp_path)
    _, client = make(base_config(version=2, blocks={"custom": {"use": "plugin"}}))

    response = client.get("/api/about", headers=OWNER)
    assert response.status_code == 200
    assert response.json() == {
        "version": version("swb-one"), "commit": "unknown", "build_date": "unknown",
        "blocks": [{"name": "custom", "id": "example", "version": "1.2.3"},
                   {"name": "mcp", "id": "mcp", "version": version("swb-one")},
                   {"name": "webhook", "id": "webhook", "version": version("swb-one")}],
    }


def test_release_label_and_build_commit_are_reported_by_all_version_endpoints(make):
    commit = "ABCDEF0" * 5 + "12345"
    _, client = make(env={**default_env(), "SWITCHBOARD_RELEASE": " 1.0.0-canary ", "GIT_COMMIT": commit})
    expected = "1.0.0-canary+abcdef0"

    assert client.get("/healthz").json()["version"] == expected
    assert client.get("/api/settings", headers=OWNER).json()["version"] == expected
    about = client.get("/api/about", headers=OWNER).json()
    assert about["version"] == expected and about["commit"] == commit.lower()
    assert {block["version"] for block in about["blocks"]} == {expected}


def test_healthz_limits_details_to_owner_browser_and_canary_key(make):
    cfg = base_config(version=2, blocks={"mcp": {}, "discord": {}})
    core, client = make(cfg, env={**default_env(), "SWITCHBOARD_KEY_OWNER": "fixture-long-owner-key-only"})
    redacted = {"status": core.health()["status"], "version": version("swb-one")}
    for headers in ({}, {"Authorization": f"Bearer {STATIC_KEY}"}, {"cf-access-jwt-assertion": "other"}):
        response = client.get("/healthz", headers=headers)
        assert response.status_code == 200 and response.json() == redacted
        assert response.headers["cache-control"] == "no-store"
    token = core.auth.sessions.mint_link("fixture", "owner")
    signed_in = client.post("/auth/link", json={"token": token}, headers={"Origin": PUBLIC})
    assert signed_in.status_code == 204
    session_cookie = signed_in.headers["set-cookie"].split(";", 1)[0]
    client.cookies.clear()
    for headers in (OWNER, {"Cookie": session_cookie}, {"Authorization": "Bearer fixture-long-owner-key-only"}):
        response = client.get("/healthz", headers=headers)
        body = response.json()
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert body["blocks"]["discord"]["online"] is False
        assert body["block_setup"]["discord"]["missing_env"] == ["DISCORD_BOT_TOKEN"]
        assert body["problems"]


@pytest.mark.parametrize("url", ["http://localhost:8870", PUBLIC])
def test_owner_healthz_notes_public_https_only_for_local_mcp(make, url):
    cfg = base_config(version=2, public_url=url, blocks={"mcp": {}})
    _, client = make(cfg)
    response = client.get("/healthz", headers=OWNER)
    assert response.status_code == 200
    block = response.json()["blocks"]["mcp"]
    assert block["next_step"] is None
    if url == PUBLIC:
        assert "manual_steps" not in block
    else:
        assert block["manual_steps"] == ["Give SwB a public HTTPS address for hosted MCP clients."]


def test_about_omits_invalid_environment_values_and_handles_missing_package(make, monkeypatch, tmp_path):
    def missing_package(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(switchboard, "version", missing_package)
    monkeypatch.setattr(api_about, "_REPO_ROOT", tmp_path / "installed")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(api_about.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not inspect the working directory"))
    _, client = make(base_config(version=2, blocks={"mcp": {}}), env={
        **default_env(), "GIT_COMMIT": "invalid-build-commit", "BUILD_DATE": "invalid-build-date",
    })

    response = client.get("/api/about", headers=OWNER)
    assert response.status_code == 200
    assert response.json() == {
        "version": "unknown", "commit": "unknown", "build_date": "unknown",
        "blocks": [{"name": "mcp", "id": "mcp", "version": None},
                   {"name": "webhook", "id": "webhook", "version": None}],
    }


def test_dev_commit_falls_back_to_the_source_checkout(monkeypatch, tmp_path):
    git_dir = tmp_path / ".git"
    git_dir.write_text("gitdir: fixture-worktree\n")
    monkeypatch.setattr(api_about, "_REPO_ROOT", tmp_path)

    def run(args, **kwargs):
        assert args == ["git", "--git-dir", str(git_dir), "rev-parse", "--verify", "HEAD"]
        return CompletedProcess(args, 0, "a" * 40 + "\n")

    monkeypatch.setattr(api_about.subprocess, "run", run)
    assert api_about._build_commit("") == "a" * 40

    def timed_out(args, *, timeout, **kwargs):
        raise TimeoutExpired(args, timeout)

    monkeypatch.setattr(api_about.subprocess, "run", timed_out)
    assert api_about._build_commit("") == "unknown"


@pytest.mark.parametrize("value,expected", [
    ("2026-11-26", "2026-11-26"),
    ("2026-11-26T18:24:00-05:00", "2026-11-26T18:24:00-05:00"),
    ("2026-02-30", "unknown"),
    ("2026-11-26T23:24:00Z\n", "unknown"),
])
def test_build_date_accepts_only_valid_dates(value, expected):
    assert api_about._build_date(value) == expected
