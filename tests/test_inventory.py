import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

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
        "avatar:claude.png", "page:/projects/:slug", "page:/pairing/code",
    } <= items.keys()
    assert {"page:/classic", "page:/canvas/*", "page:/provision/*", "page:/projects", "config_key:blocks.capy.burst"}.isdisjoint(items)
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
        [sys.executable, str(ROOT / "scripts/inventory.py"), "--check"], cwd=ROOT, capture_output=True, text=True, check=False,
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
    (docs / "cli.md").write_text("Run `swb up`.", encoding="utf-8")
    (docs / "mcp.md").write_text("Call `send`.", encoding="utf-8")
    assert inventory_coverage.main(tmp_path) == 0

    site = tmp_path / "site"
    site.mkdir()
    assert inventory_coverage.main(tmp_path) == 0
    assert "site/inventory-map.json" in capsys.readouterr().out
    (site / "inventory-map.json").write_text(json.dumps(mapping), encoding="utf-8")
    assert inventory_coverage.main(tmp_path) == 0


@pytest.mark.parametrize(
    ("key", "label", "mention", "unrelated"),
    [
        ("tool:send", "Send", "Call `send`.", "Call `send_later`."),
        ("prompt:setup-grok-bot", "Set up Grok Bot", "Open `setup-grok-bot`.", "Open `setup-grok`."),
        ("command:up", "swb up", "Run `swb up --tunnel`.", "Run `swb upgrade`."),
        ("command:swb", "swb", "Run `swb`.", "Install `swb-one`."),
        ("page:/", "SwB", "Open `/`.", "Open `/settings` or `/.well-known/*`."),
        ("page:/classic", "SwB", "Open `/classic`.", "Open `/old/classic`."),
        ("page:/projects", "SwB", "Open `/projects`.", "Open `/projects/:slug` or `/projects/one`."),
        ("page:/projects/:slug", "Project", "Open `/projects/:slug`.", "Open `/projects-old`."),
        ("page:/projects/:slug", "Project", "Open `/projects`.", "Open `/old/projects`."),
        ("page:/claim/{code}", "Save a token", "Open `https://swb.example.com/claim/abc`.", "Open `/claims`."),
        ("page:/connections/:platform", "Connections", "Open `/connections/slack`.", "Open `/connect`."),
        ("page:/agents/*", "Agents", "Open `/agents/claude`.", "Open `/agent`."),
        ("mode:triage:reply", "reply", "Choose `reply`.", "Choose `triage`."),
        ("block:claude-routine", "Claude routines", "Add Claude routines.", "Add `claude-routine`."),
        ("provider:claude-code", "Claude Code", "Connect Claude Code.", "Connect Claude."),
        ("avatar:grok-bot.png", "Grok Bot", "Choose Grok Bot.", "Choose `grok-bot.png`."),
        ("config_key:blocks.slack.workspaces.*.token_env", "", "Set `blocks.slack.workspaces.team.token_env`.", "Set `token_envs`."),
        ("config_key:blocks.slack.workspaces.*.token_env", "", "Set `token_env`.", "Set `workspaces`."),
    ],
)
def test_inventory_coverage_mentions(tmp_path, capsys, monkeypatch, key, label, mention, unrelated):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import inventory_coverage

    site = tmp_path / "site"
    site.mkdir()
    docs = tmp_path / "docs"
    docs.mkdir()
    items = [{"id": key, "label": label}]
    (docs / "inventory.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    (site / "inventory-map.json").write_text(json.dumps({key: "site/reference.mdx"}), encoding="utf-8")

    page = site / "reference.mdx"
    page.write_text(unrelated, encoding="utf-8")
    assert inventory_coverage.main(tmp_path) == 1
    assert f"Documentation page does not mention {key}: site/reference.mdx" in capsys.readouterr().err

    page.write_text(mention, encoding="utf-8")
    assert inventory_coverage.main(tmp_path) == 0


def test_inventory_coverage_hold(tmp_path, capsys, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import inventory_coverage

    site = tmp_path / "site"
    site.mkdir()
    docs = tmp_path / "docs"
    docs.mkdir()
    items = [{"id": "page:/classic"}, {"id": "tool:send"}]
    (docs / "inventory.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    hold_path = site / "inventory-hold.json"
    holds = {"page:/classic": "Legacy route"}
    hold_path.write_text(json.dumps(holds), encoding="utf-8")

    assert inventory_coverage.main(tmp_path) == 1
    assert "Missing site/inventory-map.json for held IDs: page:/classic" in capsys.readouterr().err

    map_path = site / "inventory-map.json"
    mapping = {"tool:send": "site/reference.mdx"}
    map_path.write_text(json.dumps(mapping), encoding="utf-8")
    assert inventory_coverage.main(tmp_path) == 1
    assert "Missing inventory mapping for page:/classic" in capsys.readouterr().err

    mapping["page:/classic"] = "site/reference.mdx"
    map_path.write_text(json.dumps(mapping), encoding="utf-8")
    assert inventory_coverage.main(tmp_path) == 1
    assert "Missing documentation page for page:/classic: site/reference.mdx" in capsys.readouterr().err

    page = site / "reference.mdx"
    page.write_text("Open the console.", encoding="utf-8")
    assert inventory_coverage.main(tmp_path) == 1
    output = capsys.readouterr()
    assert "Held inventory item page:/classic: site/reference.mdx" in output.out
    assert "Documentation page does not mention tool:send" in output.err

    page.write_text("Call `send`.", encoding="utf-8")
    assert inventory_coverage.main(tmp_path) == 0
    assert "Held inventory item page:/classic: site/reference.mdx (Legacy route)" in capsys.readouterr().out

    hold_path.write_text(json.dumps({**holds, "page:/removed": "Retired route"}), encoding="utf-8")
    assert inventory_coverage.main(tmp_path) == 0
    output = capsys.readouterr()
    assert "Held inventory item page:/classic" in output.out
    assert "Warning: stale inventory hold for page:/removed" in output.err

    map_path.unlink()
    hold_path.write_text(json.dumps({"page:/removed": "Retired route"}), encoding="utf-8")
    assert inventory_coverage.main(tmp_path) == 0
    output = capsys.readouterr()
    assert "coverage skipped" in output.out
    assert "Warning: stale inventory hold for page:/removed" in output.err
