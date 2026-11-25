"""HTML for the pages capy-bridge serves, in the look of the owner's sites (example.com, status, id)."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from importlib import resources
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

_STATIC = resources.files("capy_bridge") / "static"
_BRAND = json.loads((_STATIC / "brand.json").read_text())
_ASSETS = {
    "bridge.css": ("text/css; charset=utf-8", (_STATIC / "bridge.css").read_bytes()),
    "prefs.js": ("text/javascript; charset=utf-8", (_STATIC / "prefs.js").read_bytes()),
}

ARC = '<div class="site-arc" aria-hidden="true"><svg viewBox="0 300 940 580" focusable="false"><path fill="currentColor" d="M922.2,588.9A1041.1,270.9 0 1 1 -1159.9,588.9A1041.1,270.9 0 1 1 922.2,588.9ZM734.2,478.4A620,151.5 -14.18 1 0 -468.0,782.2A620,151.5 -14.18 1 0 734.2,478.4Z"/></svg></div>'
WORDMARK_DOT = '<svg class="wordmark-dot" viewBox="278.44 90.58 12.63 10.31" aria-hidden="true" focusable="false"><path fill="currentColor" d="M291.07,98.49c-.15.38-.29.76-.46,1.12-.48,1.08-1.29,1.52-2.44,1.14-.56-.19-1.14-.39-1.62-.71-2.92-1.95-5.47-4.29-7.47-7.18-.37-.53-.43-1.26-.64-1.9.15-.13.31-.25.46-.38,1.05.65,2.11,1.29,3.14,1.97.65.43,1.23.94,1.86,1.39,1.14.81,2.14.86,3.15-.33.83-.97,1.9-.61,2.67.11.58.53.91,1.33,1.35,2.02v2.75Z"/></svg>'

CSP = "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' data:; form-action 'self' https: http://localhost:* http://127.0.0.1:*; frame-ancestors 'none'; base-uri 'none'"


def icon(name: str, cls: str = "", mode: str | None = None) -> str:
    extra = f' data-mode="{mode}"' if mode else ""
    return f'<svg viewBox="0 -960 960 960" class="icon {cls}" data-icon="{name}"{extra} aria-hidden="true"><path d="{_BRAND["icons"][name]}"/></svg>'


def _signature() -> str:
    s = _BRAND["signature"]
    return f'<svg viewBox="{s["viewBox"]}" aria-hidden="true" fill="currentColor">' + "".join(f'<path d="{d}"/>' for d in s["paths"]) + "</svg>"


SIGNATURE = _signature()


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    year = datetime.now(timezone.utc).year
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#f5f5f4" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#1c1917" media="(prefers-color-scheme: dark)">
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet, noimageindex">
<meta name="referrer" content="strict-origin-when-cross-origin">
<title>{html.escape(title)} • Capy Bridge</title>
<script src="/assets/prefs.js"></script>
<link rel="stylesheet" href="/assets/bridge.css">
</head><body><div class="shell">
<div class="site-veil" aria-hidden="true"></div>{ARC}
<header class="site-header"><div class="site-head col">
<a href="https://example.com" class="brand-link"><span class="brand-sig">{SIGNATURE}</span><span class="brand-name">Capy Bridge{WORDMARK_DOT}</span></a>
</div></header>
<main class="service-main col">{body}</main>
<footer class="col fade-in"><div class="foot-row">
<div class="foot-copy">&copy; {year} &bull; Owner</div>
<div class="footer-icons">
<button type="button" class="foot-btn" aria-label="Theme" title="Theme" data-theme-cycle><span class="theme-cycle">{icon("foot_lightMode", mode="light")}{icon("foot_darkMode", mode="dark")}{icon("foot_desktopWindows", mode="system")}</span></button>
<a href="https://status.example.com" class="foot-btn" aria-label="Status" title="Status">{icon("foot_vitalSigns")}</a>
<a href="https://example.com" class="foot-btn" aria-label="Home" title="Home">{icon("foot_home")}</a>
</div></div>
<div class="foot-line"><span>Shared memory and Capy offload for the owner's assistants.</span></div>
</footer></div></body></html>"""
    return HTMLResponse(doc, status_code=status, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Content-Security-Policy": CSP, "Referrer-Policy": "no-referrer"})


def hero(title: str, line: str = "") -> str:
    return f'<section class="hero fade-in"><h1>{html.escape(title)}</h1>{f"<p>{line}</p>" if line else ""}</section>'


def notice(text: str, tone: str = "bad") -> str:
    name = {"good": "checkCircle", "bad": "errorFill", "warn": "warningFill"}[tone]
    return f'<p class="notice" role="status">{icon(name, "tone-" + tone)}<span>{html.escape(text)}</span></p>'


def message_page(title: str, text: str, status: int) -> HTMLResponse:
    return page(title, hero(title, html.escape(text)), status)


def status_page(health: dict[str, Any]) -> HTMLResponse:
    ok = health["status"] == "ok"
    rows = [
        ("Sign-in for Claude and ChatGPT", "OAuth 2.1, PKCE, DCR and CIMD", health["oauth"]),
        ("Capy offload", "Delegating to Capy through its API", health["offload"]),
        ("Memory mirror", "Markdown copy for Capy Drive", health["mirror"]),
    ]
    items = "".join(
        f'<div class="setting"><div class="setting-label"><span>{html.escape(a)}</span><span class="setting-hint">{html.escape(b)}</span></div>'
        f'<div class="setting-value">{icon("checkCircle", "tone-good") if on else icon("warningFill", "tone-warn")}<span>{"On" if on else "Off"}</span></div></div>'
        for a, b, on in rows
    )
    problems = "".join(notice(p, "warn") for p in health["problems"])
    body = hero("Capy Bridge", "Running" if ok else "Running, with parts switched off") + f'<div class="card fade-in">{items}</div>{problems}'
    return page("Status", body, 200)


async def asset(request: Request) -> Response:
    hit = _ASSETS.get(request.path_params["name"])
    if hit is None:
        return Response(status_code=404)
    return Response(hit[1], media_type=hit[0], headers={"Cache-Control": "public, max-age=3600"})


def routes() -> list[Route]:
    return [Route("/assets/{name}", asset, methods=["GET"])]
