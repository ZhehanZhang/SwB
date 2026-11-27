from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_render_generates_a_256_bit_setup_code():
    services = yaml.safe_load((ROOT / "render.yaml").read_text())["services"]
    env = next(item for item in services[0]["envVars"] if item["key"] == "SWB_SETUP_CODE")
    assert env == {"key": "SWB_SETUP_CODE", "generateValue": True}


def test_railway_generates_a_256_bit_setup_code():
    template = (ROOT / ".railway/template.env.example").read_text()
    assert 'SWB_SETUP_CODE=${{secret(64, "0123456789abcdef")}}' in template.splitlines()


@pytest.mark.parametrize(("tool", "launcher", "args", "codefile"), [
    ("fly", "fly.sh", ["test-swb"], ".env.swb-setup-fly-test-swb"),
    ("koyeb", "koyeb.sh", ["test-swb", "release/1.0"], ".env.swb-setup-koyeb-test-swb"),
    ("docker", "compose/start.sh", ["swb.example.test"], ".env"),
])
def test_launchers_generate_256_bit_codes_in_private_files(tmp_path, tool, launcher, args, codefile):
    script = tmp_path / "launcher.sh"
    shutil.copyfile(ROOT / "deploy" / launcher, script)
    executable = tmp_path / tool
    executable.write_text('#!/bin/sh\nif [ "$1" = secrets ]; then cat > "$CAPTURE"; fi\n')
    executable.chmod(0o700)
    capture = tmp_path / "upload"
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}", "CAPTURE": str(capture)}
    result = subprocess.run(["sh", "-x", str(script), *args], cwd=tmp_path,
                            env=env, check=True, capture_output=True, text=True)
    codefile = tmp_path / codefile
    assert stat.S_IMODE(codefile.stat().st_mode) == 0o600
    value = next(line.removeprefix("SWB_SETUP_CODE=") for line in codefile.read_text().splitlines()
                 if line.startswith("SWB_SETUP_CODE="))
    valid_format = bool(re.fullmatch(r"[a-f0-9]{64}", value))
    assert valid_format
    leaked = value in result.stdout or value in result.stderr
    assert not leaked
    if tool != "docker":
        uploaded = capture.read_text().strip().removeprefix("SWB_SETUP_CODE=")
        received = uploaded == value
        assert received
