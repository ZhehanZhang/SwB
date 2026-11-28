import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "fly.toml"
PUBLIC_IPS = {"shared_v4", "v6"}


def fly(*args, capture=False, stdin=None):
    return subprocess.run(
        ["fly", *args], cwd=ROOT, check=True, text=True, stdin=stdin, capture_output=capture
    ).stdout


def resources(app, resource):
    try:
        result = json.loads(fly(resource, "list", "--json", "--app", app, capture=True))
    except subprocess.CalledProcessError as exc:
        if exc.stderr:
            print(exc.stderr.rstrip(), file=sys.stderr)
        raise ValueError(f"Could not list Fly {resource} for {app}.") from None
    except json.JSONDecodeError:
        raise ValueError(f"Fly {resource} list returned invalid JSON. Update flyctl and try again.") from None
    if result is None:
        return []
    if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
        raise ValueError(f"Fly {resource} list returned an unexpected JSON shape.")
    return result


def check_app(app, region, mount, *, deployed=False, repair=True):
    volumes = resources(app, "volumes")
    if len(volumes) > 1 or (deployed and len(volumes) != 1):
        raise ValueError(f"Expected one Fly volume after deploy; found {len(volumes)}. Review volumes in Fly.")
    volume = volumes[0] if volumes else {}
    if volume and (
        (volume.get("name"), volume.get("size_gb"), volume.get("region"), volume.get("state"))
        != (mount["source"], 1, region, "created")
        or not volume.get("id") or volume.get("host_dedication_id")
    ):
        raise ValueError(f"Expected a ready 1 GB {mount['source']} volume in {region}. Review the Fly volume.")

    ip_types = [ip.get("Type") for ip in resources(app, "ips")]
    if any(kind not in PUBLIC_IPS for kind in ip_types) or len(set(ip_types)) != len(ip_types):
        raise ValueError(
            "Expected only one shared IPv4 and one public IPv6. Review Fly IPs, including dedicated IPv4 and static egress."
        )

    machines = [m for m in resources(app, "machines") if m.get("state") != "destroyed"]
    if machines:
        primary = next((m for m in machines if m.get("id") == volume.get("attached_machine_id")), None)
        if not primary or primary.get("region") != region:
            raise ValueError(f"Expected the Fly Machine that holds the data volume in {region}. Review Machines and volumes.")
        config = primary.get("config") or {}
        metadata = config.get("metadata") or {}
        group = metadata.get("fly_process_group", metadata.get("process_group"))
        if metadata.get("fly_platform_version") != "v2" or group != "app":
            raise ValueError("Expected a Fly Launch managed Machine in the app process group.")
        mounts = config.get("mounts") or []
        if (
            len(mounts) != 1
            or (mounts[0].get("volume"), mounts[0].get("path")) != (volume["id"], mount["destination"])
            or (deployed and mounts[0].get("extend_threshold_percent"))
        ):
            raise ValueError("Expected the Fly Machine to mount only the 1 GB data volume with extension off.")
        extras = [m for m in machines if m is not primary]
        if extras and not repair:
            raise ValueError("Fly still reports an extra Machine after removing the unmounted standby.")
        for extra in extras:
            extra_config = extra.get("config") or {}
            if not (
                extra.get("id") and extra["id"] != primary["id"]
                and extra_config.get("standbys") == [primary["id"]]
                and not extra_config.get("mounts")
                and extra.get("state") in {"stopped", "suspended"}
            ):
                raise ValueError(f"Expected one Fly Machine; found {len(machines)}. Review the extra Machines in Fly.")
        for extra in extras:
            fly("machine", "destroy", extra["id"], "--app", app)
        if extras:
            return check_app(app, region, mount, deployed=deployed, repair=False)
        if deployed:
            if primary.get("state") != "started":
                raise ValueError("Expected the Fly Machine to be running after deploy.")
            guest = config.get("guest") or {}
            if (
                (guest.get("cpu_kind"), guest.get("cpus"), guest.get("memory_mb")) != ("shared", 1, 512)
                or any(guest.get(key) for key in ("gpu_kind", "gpus", "host_dedication_id"))
            ):
                raise ValueError("Expected shared-cpu-1x with 512 MB. Review the Fly Machine size.")
            services = config.get("services") or []
            if len(services) != 1 or (services[0].get("autostop") is not False and services[0].get("autostop") != "off"):
                raise ValueError("Expected one Fly service with automatic stop off.")
    elif deployed or volume.get("attached_machine_id"):
        raise ValueError(f"Expected one Fly Machine after deploy; found {len(machines)}. Review Machines in Fly.")

    if deployed and set(ip_types) != PUBLIC_IPS:
        raise ValueError("Expected both shared IPv4 and public IPv6 after deploy. Review Fly IPs.")
    return set(ip_types)


def main():
    parser = argparse.ArgumentParser(prog="sh deploy/fly.sh")
    parser.add_argument("app")
    parser.add_argument("--existing", action="store_true", help="deploy to an existing SwB app without changing secrets")
    args = parser.parse_args()
    app = args.app
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", app):
        parser.error("Use lowercase letters, digits and hyphens for the app name.")

    spec = tomllib.loads(CONFIG.read_text())
    region, mount = spec.get("primary_region"), spec.get("mounts")
    if (
        region != "iad" or mount != {"source": "switchboard_data", "destination": "/data", "initial_size": 1}
        or spec.get("vm") != [{"size": "shared-cpu-1x", "memory": "512mb"}]
        or spec.get("http_service", {}).get("auto_stop_machines") != "off"
        or spec.get("deploy") != {"strategy": "rolling"}
        or any(key in spec for key in ("processes", "services", "host_dedication_id"))
    ):
        raise ValueError("fly.toml does not match the one-Machine SwB deploy shape.")

    if not args.existing:
        setup = Path(f".env.swb-setup-fly-{app}")
        with os.fdopen(os.open(setup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
            file.write(f"SWB_SETUP_CODE={secrets.token_hex(32)}\n")
        try:
            fly("apps", "create", app)
        except (OSError, subprocess.CalledProcessError):
            setup.unlink()
            raise
        print(f"Setup code saved in {setup}", flush=True)
        with setup.open() as file:
            fly("secrets", "import", "--stage", "--app", app, stdin=file)

    ip_types = check_app(app, region, mount)
    if "shared_v4" not in ip_types:
        fly("ips", "allocate-v4", "--shared", "--yes", "--app", app)
    if "v6" not in ip_types:
        fly("ips", "allocate-v6", "--app", app)
    fly("deploy", "--ha=false", "--no-public-ips", "--config", "fly.toml", "--app", app)
    check_app(app, region, mount, deployed=True)
    print(f"Verified one Fly Machine and volume in {region}, with shared IPv4 and public IPv6.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"Fly deploy guard: {error}")
