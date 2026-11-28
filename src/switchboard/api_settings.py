from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .api_runtime import runtime_info
from .deployment import PLATFORM
from .doc_links import public_origin
from .env import env_value

_DOC_PLATFORMS = {"fly", "render", "railway", "compose", "local"}

async def settings(request: Request) -> JSONResponse:
    core = request.app.state.core
    runtime = runtime_info(core)
    platform = env_value(core.cfg.env, PLATFORM)
    owner_configured = (
        any(key.name == "owner" for key in core.cfg.static_keys)
        and all("owner" not in limits for limits in (
            core.cfg.inbox_keys, core.cfg.key_scopes, core.cfg.key_tools, core.cfg.key_targets,
        ))
    )
    return JSONResponse({
        "version": runtime["version"],
        "mcp_url": runtime["mcp_url"],
        "deploy_platform": platform if platform in _DOC_PLATFORMS else None,
        "public_url": public_origin(core.cfg.public_url),
        "data_dir": str(core.data_dir.resolve()),
        "owner_key_masked": "••••••••" if owner_configured else None,
        "host": runtime["host"],
        "uptime_seconds": runtime["uptime_seconds"],
    })


SETTINGS_ROUTES = [Route("/settings", settings)]
