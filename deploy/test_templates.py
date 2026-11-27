from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_deploy_templates_use_the_wildcard_docker_runtime():
    docker = (ROOT / "Dockerfile").read_text()
    runtime, paas = docker.split("FROM base AS runtime", 1)[1].split("FROM runtime AS paas", 1)
    assert re.search(r"^ENV .*\bPORT=8000(?: |$)", runtime, flags=re.M)
    assert re.search(r"^ENV .*\bSWB_DEFAULT_HOST=0\.0\.0\.0(?: |$)", runtime, flags=re.M)
    assert re.search(r"^ENV .*\bSWB_DEFAULT_DATA_DIR=/data(?: |$)", runtime, flags=re.M)
    assert 'CMD ["swb", "serve"]' in runtime and 'CMD ["swb", "serve"]' in paas

    render = yaml.safe_load((ROOT / "render.yaml").read_text())["services"][0]
    assert render["runtime"] == "docker"
    assert tomllib.loads((ROOT / "fly.toml").read_text())["build"]["build-target"] == "paas"
    assert json.loads((ROOT / "railway.json").read_text())["build"]["builder"] == "DOCKERFILE"
    assert "--git-docker-target paas" in (ROOT / "deploy/koyeb.sh").read_text()

    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]["switchboard"]
    assert compose["build"]["target"] == "runtime"
    assert compose["environment"]["SWB_HOST"] == "0.0.0.0"
    assert compose["environment"]["SWB_PORT"] == ""
    assert compose["environment"]["PORT"] == "8000"
    published = "${SWB_BIND:-${SWITCHBOARD_BIND:-127.0.0.1}}:${SWB_PORT:-${SWITCHBOARD_PORT:-8870}}"
    assert compose["environment"]["SWB_COMPOSE_ORIGIN"] == f"http://{published}"
    assert compose["ports"] == [f"{published}:8000"]
    caddy = yaml.safe_load((ROOT / "deploy/compose/compose.yaml").read_text())["services"]["switchboard"]
    assert caddy["build"]["target"] == "paas"
    assert caddy["environment"]["SWB_KEY_OWNER"] == "${SWB_KEY_OWNER-${SWITCHBOARD_KEY_OWNER-}}"
    assert "SWITCHBOARD_KEY_OWNER" not in caddy["environment"]
    for service in (compose, caddy):
        assert service["environment"]["SWB_TRUSTED_PROXIES"] is None


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
