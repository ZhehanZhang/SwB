from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
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


def test_render_uses_one_paid_instance_with_one_disk():
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text())
    assert set(blueprint) == {"services"}
    (service,) = blueprint["services"]
    assert service["plan"] == "0.5c-512mb"
    assert service["numInstances"] == 1
    assert "scaling" not in service
    assert "previews" not in service
    assert service["disk"] == {"name": "switchboard-data", "mountPath": "/data", "sizeGB": 1}


def test_railway_generates_a_256_bit_setup_code():
    template = (ROOT / ".railway/template.env.example").read_text()
    assert 'SWB_SETUP_CODE=${{secret(64, "0123456789abcdef")}}' in template.splitlines()


def test_railway_uses_one_always_on_replica_with_one_volume():
    settings = json.loads((ROOT / "railway.json").read_text())
    assert settings["deploy"] == {
        "healthcheckPath": "/healthz",
        "numReplicas": 1,
        "sleepApplication": False,
        "overlapSeconds": 0,
        "requiredMountPath": "/data",
    }
    iac = (ROOT / ".railway/railway.ts").read_text()
    assert 'source: github("ZhehanZhang/SwB", { branch: "release/1.0" })' in iac
    assert 'deploy: { requiredMountPath: "/data", sleepApplication: false, overlapSeconds: 0 }' in iac
    assert "replicas: 1," in iac
    assert 'volume("switchboard-data", { sizeMB: 1024 })' in iac
    assert 'volumeMounts: { "/data": data }' in iac
    assert "resources: [swb, data]" in iac
    assert "SWB_DATA_DIR=/data" in (ROOT / ".railway/template.env.example").read_text().splitlines()


@pytest.mark.parametrize(("tool", "launcher", "args", "codefile"), [
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


@pytest.fixture
def mock_fly(tmp_path):
    executable = tmp_path / "fly"
    executable.write_text(f"""#!{sys.executable}
import json
import os
import sys
from pathlib import Path

root = Path(os.environ["MOCK_FLY_DIR"])
args = sys.argv[1:]
with (root / "calls").open("a") as calls:
    print(json.dumps(args), file=calls)
data = json.loads((root / "data.json").read_text())
phase = "after" if (root / "deployed").exists() else "before"
if args[1:2] == ["list"]:
    rows = data[phase][args[0]]
    if args[0] == "machines" and (root / "destroyed").exists():
        rows = [machine for machine in rows if machine["id"] != (root / "destroyed").read_text()]
    print(json.dumps(rows))
elif args[:2] == ["machine", "destroy"]:
    (root / "destroyed").write_text(args[2])
elif args[0] == "deploy":
    (root / "deployed").touch()
elif args[:2] == ["secrets", "import"]:
    (root / "upload").write_text(sys.stdin.read())
elif args[:2] not in [["apps", "create"], ["ips", "allocate-v4"], ["ips", "allocate-v6"]]:
    sys.exit(3)
""")
    executable.chmod(0o700)
    shape = {
        "volumes": [{
            "id": "vol_swb", "name": "switchboard_data", "size_gb": 1, "region": "iad", "state": "created",
            "attached_machine_id": "swb-primary",
        }],
        "ips": [{"Type": "shared_v4", "Address": "203.0.113.1"}, {"Type": "v6", "Address": "2001:db8::1"}],
        "machines": [{
            "id": "swb-primary", "region": "iad", "state": "started",
            "config": {
                "metadata": {"fly_platform_version": "v2", "fly_process_group": "app"},
                "guest": {"cpu_kind": "shared", "cpus": 1, "memory_mb": 512},
                "mounts": [{"volume": "vol_swb", "path": "/data"}],
                "services": [{"autostop": False}],
            },
        }],
    }

    def run(*, existing=False, before=None, after=None):
        (tmp_path / "data.json").write_text(json.dumps({
            "before": {resource: [] for resource in shape} if before is None else before,
            "after": shape if after is None else after,
        }))
        env = {
            **os.environ,
            "PATH": os.pathsep.join((str(tmp_path), str(Path(sys.executable).parent), os.environ["PATH"])),
            "MOCK_FLY_DIR": str(tmp_path),
        }
        command = ["sh", "-x", str(ROOT / "deploy/fly.sh"), "test-swb"]
        if existing:
            command.append("--existing")
        result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True)
        calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
        return result, calls

    return run, shape


def test_fly_creates_and_verifies_the_guarded_shape(tmp_path, mock_fly):
    run, _ = mock_fly
    result, calls = run()
    assert result.returncode == 0, result.stderr
    assert "Verified one Fly Machine and volume in iad" in result.stdout
    assert calls[0] == ["apps", "create", "test-swb"]
    assert ["ips", "allocate-v4", "--shared", "--yes", "--app", "test-swb"] in calls
    assert ["ips", "allocate-v6", "--app", "test-swb"] in calls
    deploy = ["deploy", "--ha=false", "--no-public-ips", "--config", "fly.toml", "--app", "test-swb"]
    assert calls[calls.index(deploy) + 1:] == [
        [resource, "list", "--json", "--app", "test-swb"] for resource in ("volumes", "ips", "machines")
    ]
    codefile = tmp_path / ".env.swb-setup-fly-test-swb"
    code = codefile.read_text().strip().removeprefix("SWB_SETUP_CODE=")
    assert stat.S_IMODE(codefile.stat().st_mode) == 0o600
    assert bool(re.fullmatch(r"[a-f0-9]{64}", code))
    assert bool((tmp_path / "upload").read_text() == f"SWB_SETUP_CODE={code}\n")
    assert not (code in result.stdout or code in result.stderr)


@pytest.mark.parametrize(("resource", "drift", "message"), [
    ("volumes", {"size_gb": 2}, "ready 1 GB"),
    ("volumes", {"region": "lax"}, "in iad"),
    ("ips", {"Type": "v4"}, "dedicated IPv4"),
    ("ips", {"Type": "egress_v6"}, "static egress"),
    ("machines", {"region": "lax"}, "volume in iad"),
    ("machines", {"config": {}}, "Fly Launch managed"),
])
def test_fly_stops_on_unsafe_drift_before_deploy(mock_fly, resource, drift, message):
    run, shape = mock_fly
    shape[resource][0].update(drift)
    result, calls = run(existing=True, before=shape)
    assert result.returncode != 0
    assert message in result.stderr
    assert all(call[1] == "list" for call in calls)


@pytest.mark.parametrize(("drift", "message"), [
    ("volume", "found 2"),
    ("machine", "found 2"),
    ("size", "shared-cpu-1x with 512 MB"),
    ("stop", "automatic stop off"),
    ("ip", "dedicated IPv4"),
    ("missing-ip", "both shared IPv4 and public IPv6"),
    ("off", "to be running"),
])
def test_fly_fails_postflight_on_drift(mock_fly, drift, message):
    run, shape = mock_fly
    config = shape["machines"][0]["config"]
    if drift == "volume":
        shape["volumes"].append({**shape["volumes"][0], "id": "vol_extra"})
    elif drift == "machine":
        shape["machines"].append({"id": "swb-extra", "region": "iad", "state": "started"})
    elif drift == "size":
        config["guest"]["memory_mb"] = 1024
    elif drift == "stop":
        config["services"][0]["autostop"] = "stop"
    elif drift == "ip":
        shape["ips"].append({"Type": "v4"})
    elif drift == "missing-ip":
        shape["ips"].pop()
    else:
        shape["machines"][0]["state"] = "stopped"
    result, calls = run(after=shape)
    assert result.returncode != 0
    assert message in result.stderr
    assert any(call[0] == "deploy" for call in calls)
    assert all(call[1:2] != ["release"] for call in calls)


@pytest.mark.parametrize("phase", ["before", "after"])
def test_fly_removes_only_an_unmounted_standby(mock_fly, phase):
    run, shape = mock_fly
    drift = {**shape, "machines": [*shape["machines"], {
        "id": "swb-extra", "region": "iad", "state": "stopped", "config": {"standbys": ["swb-primary"]},
    }]}
    result, calls = run(existing=True, **{phase: drift})
    assert result.returncode == 0, result.stderr
    assert ["machine", "destroy", "swb-extra", "--app", "test-swb"] in calls
    assert all(call[:2] != ["secrets", "import"] for call in calls)
