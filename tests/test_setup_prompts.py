import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import STATIC_KEY, base_config, call, payload, rpc

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("docs/prompts")
BUNDLED = Path("src/switchboard/prompts/setup")
SYNC = Path("scripts/sync_prompts.py")


def test_setup_prompts_and_resources_match_docs(make):
    _, client = make(config=base_config(version=2, blocks={"mcp": {}}))
    prompts = {
        row["name"]: row for row in rpc(client, STATIC_KEY, "prompts/list").json()["result"]["prompts"]
        if row["name"].startswith("setup-")
    }
    resources = {
        row["uri"]: row for row in rpc(client, STATIC_KEY, "resources/list").json()["result"]["resources"]
        if row["uri"].startswith("swb://setup/")
    }
    sources = sorted((ROOT / SOURCE).glob("*.md"))
    assert sources
    assert set(prompts) == {f"setup-{path.stem}" for path in sources}
    assert set(resources) == {f"swb://setup/{path.stem}" for path in sources}
    assert prompts["setup-grok-bot"]["title"] == "Set up Grok Bot"
    for path in sources:
        text = path.read_bytes().decode("utf-8")
        name, uri = f"setup-{path.stem}", f"swb://setup/{path.stem}"
        result = rpc(client, STATIC_KEY, "prompts/get", {"name": name}).json()["result"]
        assert result["messages"] == [{"role": "user", "content": {"type": "text", "text": text}}]
        assert not prompts[name].get("arguments")
        assert resources[uri]["mimeType"] == "text/markdown"
        result = rpc(client, STATIC_KEY, "resources/read", {"uri": uri}).json()["result"]
        assert result["contents"] == [{"uri": uri, "mimeType": "text/markdown", "text": text}]


@pytest.mark.parametrize(("what", "kind"), [("grok-bot", "grok-bot"), ("grok-routine", "grok-bot"), ("deploy", "deploy")])
def test_connect_returns_setup_guide(make, what, kind):
    _, client = make(config=base_config(version=2, blocks={"mcp": {}}))
    out = payload(call(client, STATIC_KEY, "connect", {"what": what}))
    assert out["what"] == kind
    assert out["guide"] == {"prompt": f"setup-{kind}", "resource": f"swb://setup/{kind}"}
    assert out["complete"] is False


def test_connect_unknown_lists_setup_guides(make):
    _, client = make(config=base_config(version=2, blocks={"mcp": {}}))
    out = payload(call(client, STATIC_KEY, "connect", {"what": "unknown"}))
    assert out["what"] == "unknown"
    assert out["guides"] == sorted(f"setup-{path.stem}" for path in (ROOT / SOURCE).glob("*.md"))


def test_sync_setup_prompts_check_and_repair(tmp_path):
    def run(root, *args):
        return subprocess.run([sys.executable, str(root / SYNC), *args], cwd=root, capture_output=True)

    result = run(ROOT, "--check")
    assert result.returncode == 0, result.stderr
    (tmp_path / SYNC).parent.mkdir()
    shutil.copyfile(ROOT / SYNC, tmp_path / SYNC)
    shutil.copytree(ROOT / SOURCE, tmp_path / SOURCE)
    shutil.copytree(ROOT / BUNDLED, tmp_path / BUNDLED)

    modified = tmp_path / BUNDLED / "grok-bot.md"
    modified.write_bytes(b"changed\r\n")
    result = run(tmp_path, "--check")
    assert result.returncode == 1 and b"stale" in result.stderr
    assert modified.read_bytes() == b"changed\r\n"

    stale = tmp_path / BUNDLED / "retired.md"
    stale.touch()
    missing = tmp_path / BUNDLED / "grok.md"
    missing.unlink()
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert not stale.exists()
    assert missing.is_file()
    result = run(tmp_path, "--check")
    assert result.returncode == 0, result.stderr
