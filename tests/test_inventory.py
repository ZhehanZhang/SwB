import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


async def test_generated_inventory(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from inventory import generate

    inventory = json.loads(await generate())
    assert inventory["schema_version"] == 1
    items = {row["id"]: row for row in inventory["items"]}
    assert list(items) == sorted(items)
    assert {
        "block:discord", "provider:grok", "tool:send", "tool:slack_post", "mode:triage", "mode:triage:reply",
        "page:/settings", "page:/tokens", "page:/inbox", "command:up", "config_key:blocks.slack.workspaces.*.identities.bot.token_env",
        "avatar:claude.png",
    } <= items.keys()
    assert "page:/classic" not in items
    assert {"task", "to"} <= set(items["tool:send"]["input_properties"])
    assert "ctx" not in items["tool:send"]["input_properties"]
    assert "--tunnel" in items["command:up"]["flags"]
    slack = items["block:slack"]["credentials"]
    assert {"env": "SLACK_<WORKSPACE>_APP_TOKEN", "claim_label": "App-level token"} in slack
    avatar = items["avatar:claude.png"]
    assert avatar["sha256"] == hashlib.sha256((ROOT / avatar["icon"]).read_bytes()).hexdigest()
    assert items["provider:claude"]["icon"] == avatar["icon"]
    assert all(not row["icon"] or (ROOT / row["icon"]).is_file() for row in items.values())


def test_inventory_check_passes():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/inventory.py"), "--check"], cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_inventory_coverage(tmp_path, capsys, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import inventory_coverage

    docs = tmp_path / "docs"
    docs.mkdir()
    inventory = {"items": [{"id": "tool:send"}, {"id": "command:up"}]}
    (docs / "inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
    assert inventory_coverage.main(tmp_path) == 0
    assert "coverage skipped" in capsys.readouterr().out

    mapping = {"command:up": "docs/cli.md"}
    map_path = docs / "inventory-map.json"
    map_path.write_text(json.dumps(mapping), encoding="utf-8")
    assert inventory_coverage.main(tmp_path) == 1
    errors = capsys.readouterr().err
    assert "Missing inventory mapping for tool:send" in errors
    assert "Missing documentation page for command:up" in errors

    mapping["tool:send"] = "docs/mcp.md"
    map_path.write_text(json.dumps(mapping), encoding="utf-8")
    (docs / "cli.md").touch()
    (docs / "mcp.md").touch()
    assert inventory_coverage.main(tmp_path) == 0

    site = tmp_path / "site"
    site.mkdir()
    assert inventory_coverage.main(tmp_path) == 0
    assert "site/inventory-map.json" in capsys.readouterr().out
    (site / "inventory-map.json").write_text(json.dumps(mapping), encoding="utf-8")
    assert inventory_coverage.main(tmp_path) == 0
