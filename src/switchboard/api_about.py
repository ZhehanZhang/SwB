from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from datetime import datetime
from importlib.metadata import entry_points
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import installed_version
from .blocks import BLOCKS
from .env import env_value

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMMIT = re.compile(r"[0-9a-fA-F]{7,64}")
_BUILD_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-5][0-9]))?")


def _build_commit(value: str) -> str:
    if _COMMIT.fullmatch(value):
        return value.lower()
    git_dir = _REPO_ROOT / ".git"
    if git_dir.exists():
        try:
            commit = subprocess.run(["git", "--git-dir", str(git_dir), "rev-parse", "--verify", "HEAD"],
                                    capture_output=True, encoding="ascii", errors="replace", check=True, timeout=2).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        if _COMMIT.fullmatch(commit):
            return commit.lower()
    return "unknown"


def _build_date(value: str) -> str:
    if _BUILD_DATE.fullmatch(value):
        try:
            datetime.fromisoformat(value)
            return value
        except ValueError:
            pass
    return "unknown"


def release_version(env: Mapping[str, str]) -> str | None:
    if label := (env_value(env, "SWB_RELEASE") or "").strip():
        commit = _build_commit(env.get("GIT_COMMIT", ""))
        return label if commit == "unknown" else f"{label}+{commit[:7]}"
    return installed_version()


def about(request: Request) -> JSONResponse:
    core = request.app.state.core
    release = release_version(core.cfg.env)
    blocks = core.visible_blocks()
    block_types = {name: str(core.cfg.blocks.get(name, {}).get("use") or block.manifest.id) for name, block in blocks.items()}
    block_versions = dict.fromkeys(BLOCKS, release)
    plugin_types = set(block_types.values()) - BLOCKS.keys()
    if plugin_types:
        for ep in entry_points(group="switchboard.blocks"):
            if ep.name in plugin_types and ep.name not in block_versions:
                block_versions[ep.name] = ep.dist.version if ep.dist else None
    return JSONResponse({
        "version": release or "unknown",
        "commit": _build_commit(core.cfg.env.get("GIT_COMMIT", "")),
        "build_date": _build_date(core.cfg.env.get("BUILD_DATE", "")),
        "blocks": [
            {"name": name, "id": block.manifest.id, "version": block_versions.get(block_types[name])}
            for name, block in sorted(blocks.items())
        ],
    })


ABOUT_ROUTES = [Route("/about", about, methods=["GET"])]
