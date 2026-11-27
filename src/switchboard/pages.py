"""Server-rendered Switchboard pages."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import math
from datetime import datetime, timezone
from importlib import resources
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

from .slack.avatar_files import agent_avatar_url

_STATIC = resources.files("switchboard") / "static"
_BRAND = json.loads((_STATIC / "brand.json").read_text())
_ASSETS = {
    "bridge.css": ("text/css; charset=utf-8", (_STATIC / "bridge.css").read_bytes()),
    "prefs.js": ("text/javascript; charset=utf-8", (_STATIC / "prefs.js").read_bytes()),
    "claim.css": ("text/css; charset=utf-8", (_STATIC / "claim.css").read_bytes()),
    "theme.js": ("text/javascript; charset=utf-8", (_STATIC / "theme.js").read_bytes()),
    "claim-brand.svg": ("image/svg+xml", (_STATIC / "claim-brand.svg").read_bytes()),
    "claim-connectors.svg": ("image/svg+xml", (_STATIC / "claim-connectors.svg").read_bytes()),
}

ARC = '<div class="site-arc" aria-hidden="true"><svg viewBox="0 300 940 580" focusable="false"><path fill="currentColor" d="M922.2,588.9A1041.1,270.9 0 1 1 -1159.9,588.9A1041.1,270.9 0 1 1 922.2,588.9ZM734.2,478.4A620,151.5 -14.18 1 0 -468.0,782.2A620,151.5 -14.18 1 0 734.2,478.4Z"/></svg></div>'
WORDMARK_DOT = '<svg class="wordmark-dot" viewBox="278.44 90.58 12.63 10.31" aria-hidden="true" focusable="false"><path fill="currentColor" d="M291.07,98.49c-.15.38-.29.76-.46,1.12-.48,1.08-1.29,1.52-2.44,1.14-.56-.19-1.14-.39-1.62-.71-2.92-1.95-5.47-4.29-7.47-7.18-.37-.53-.43-1.26-.64-1.9.15-.13.31-.25.46-.38,1.05.65,2.11,1.29,3.14,1.97.65.43,1.23.94,1.86,1.39,1.14.81,2.14.86,3.15-.33.83-.97,1.9-.61,2.67.11.58.53.91,1.33,1.35,2.02v2.75Z"/></svg>'

CSP = "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' data:; form-action 'self' https: http://localhost:* http://127.0.0.1:*; frame-ancestors 'none'; base-uri 'none'"
CLAIM_BRAND = '<img class="brand" src="/assets/claim-brand.svg" alt="SwB" width="56" height="56">'
CLAIM_THEME_META = (
    '<meta name="color-scheme" content="light dark">\n'
    '<meta name="theme-color" media="(prefers-color-scheme: light)" content="#ffffff">\n'
    '<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#09090b">'
)
CLAIM_CONNECTORS = {
    "slack": ("Slack", "Slack token"),
    "discord": ("Discord", "Discord bot token"),
    "telegram": ("Telegram", "Telegram bot token"),
    "github": ("GitHub", "GitHub credential"),
    "chatgpt": ("ChatGPT", ""),
    "gemini": ("Gemini", ""),
    "muse": ("Muse", "Muse bridge token"),
    "claude": ("Claude", ""),
    "grok": ("Grok", "Grok credential"),
}

LOGIN_TITLE = "Sign in to SwB"
AUTHORIZE_TITLE = "Connect"
PAIRING_TITLE = "Pairing code"
TOKEN_CLAIM_TITLE = "Save a token"
ROUTINE_CLAIM_TITLE = "Claude routine token"


class PageRoute(Route):
    def __init__(self, path: str, endpoint, title: str, *, methods: list[str]):
        super().__init__(path, endpoint, methods=methods)
        self.title = title


def icon(name: str, cls: str = "", mode: str | None = None) -> str:
    extra = f' data-mode="{mode}"' if mode else ""
    return f'<svg viewBox="0 -960 960 960" class="icon {cls}" data-icon="{name}"{extra} aria-hidden="true"><path d="{_BRAND["icons"][name]}"/></svg>'


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    year = datetime.now(timezone.utc).year
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#f5f5f4" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#1c1917" media="(prefers-color-scheme: dark)">
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet, noimageindex">
<meta name="referrer" content="same-origin">
<title>{html.escape(title)} | SwB</title>
<script src="/assets/theme.js"></script><script src="/assets/prefs.js" defer></script>
<link rel="stylesheet" href="/assets/bridge.css">
</head><body><div class="shell">
<div class="site-veil" aria-hidden="true"></div>{ARC}
<header class="site-header"><div class="site-head col">
<a href="/" class="brand-link"><span class="brand-name">SwB{WORDMARK_DOT}</span></a>
</div></header>
<main class="service-main col">{body}</main>
<footer class="col fade-in"><div class="foot-row">
<div class="foot-copy">&copy; {year} SwB</div>
<div class="footer-icons">
<button type="button" class="foot-btn" aria-label="Theme" title="Theme" data-theme-cycle><span class="theme-cycle">{icon("foot_lightMode", mode="light")}{icon("foot_darkMode", mode="dark")}{icon("foot_desktopWindows", mode="system")}</span></button>
</div></div>
<div class="foot-line"><span>Shared memory and routing between assistants and agents.</span></div>
</footer></div></body></html>"""
    return HTMLResponse(doc, status_code=status, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Content-Security-Policy": CSP, "Referrer-Policy": "same-origin"})


def hero(title: str, line: str = "") -> str:
    return f'<section class="hero fade-in"><h1>{html.escape(title)}</h1>{f"<p>{line}</p>" if line else ""}</section>'


def message_page(title: str, text: str, status: int) -> HTMLResponse:
    return page(title, hero(title, html.escape(text)), status)


PASSKEY_JS = """
async function post(path, body, csrf) {
  const headers = {"Content-Type": "application/json"};
  if (csrf) headers["X-CSRF-Token"] = csrf;
  const response = await fetch(path, {method: "POST", headers, body: JSON.stringify(body)});
  if (!response.ok) throw new Error("Request refused");
  return response.status === 204 ? null : response.json();
}
function decode(value) {
  return Uint8Array.from(atob(value.replaceAll("-", "+").replaceAll("_", "/")), c => c.charCodeAt(0));
}
function encode(value) {
  return btoa(String.fromCharCode(...new Uint8Array(value))).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}
async function passkey(action, csrf) {
  const path = "/auth/passkey/" + action;
  const {publicKey} = await post(path + "/options", {}, csrf);
  const register = action === "register";
  const parser = register ? "parseCreationOptionsFromJSON" : "parseRequestOptionsFromJSON";
  let options = publicKey;
  if (PublicKeyCredential[parser]) {
    options = PublicKeyCredential[parser](publicKey);
  } else {
    options.challenge = decode(options.challenge);
    if (register) options.user.id = decode(options.user.id);
    for (const item of (register ? options.excludeCredentials : options.allowCredentials) || []) item.id = decode(item.id);
  }
  const credential = await navigator.credentials[register ? "create" : "get"]({publicKey: options});
  if (!credential) throw new Error("No credential");
  const response = credential.response;
  const fields = register ? ["attestationObject"] : ["authenticatorData", "signature", "userHandle"];
  const result = credential.toJSON ? credential.toJSON() : {
    id: credential.id, rawId: encode(credential.rawId), type: credential.type,
    response: Object.fromEntries(["clientDataJSON", ...fields].map(field => [field, response[field] ? encode(response[field]) : null]))
  };
  await post(path + "/verify", {credential: result, ...(register ? {name: "Passkey"} : {})}, csrf);
}
"""

LOGIN_JS = PASSKEY_JS + """
const screen = document.querySelector("main");
const message = document.querySelector("#login-error");
const button = document.querySelector("#passkey-login");
const setup = document.querySelector("#owner-setup");
const fragment = new URLSearchParams(location.hash.slice(1));
const next = () => location.replace(screen.dataset.next);
if (setup) setup.addEventListener("submit", async (event) => {
  event.preventDefault();
  const code = new FormData(setup).get("code");
  setup.reset();
  const submit = setup.querySelector("button");
  submit.disabled = true;
  message.textContent = "";
  try { await post("/auth/setup", {code: code.trim()}); next(); }
  catch { message.textContent = "Check your setup code or try again later."; }
  finally { submit.disabled = false; }
});
if (button) button.addEventListener("click", async () => {
  button.disabled = true;
  message.textContent = "";
  try { await passkey("login"); next(); }
  catch { message.textContent = "Try your passkey again."; }
  finally { button.disabled = false; }
});
if (fragment.has("t")) {
  history.replaceState(null, "", location.pathname + location.search);
  if (button) button.disabled = true;
  post("/auth/link", {token: fragment.get("t")}).then(next).catch(() => {
    message.textContent = "Request a fresh sign-in link.";
    screen.hidden = false;
    if (button) button.disabled = false;
  });
} else if (screen.hidden) {
  next();
}
"""

def script_policy(script: str) -> str:
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    return CSP.replace("script-src 'self'", f"script-src 'self' 'sha256-{digest}'; connect-src 'self'")


def login_page(methods: dict[str, Any], next_path: str, error: str | None, *, signed_in: bool = False) -> HTMLResponse:
    messages = {"link_expired": "Request a fresh sign-in link.", "passkey_failed": "Try your passkey again.",
                "oidc_failed": "Try signing in again."}
    button = '<button type="button" id="passkey-login">Sign in with a passkey</button>' if methods["passkeys"] else ""
    oidc = methods["oidc"]
    if oidc:
        href = "/auth/oidc/start?" + urlencode({"next": next_path})
        button += f'<p><a href="{html.escape(href)}">Continue with {html.escape(oidc["name"])}</a></p>'
    if methods.get("setup"):
        button += ('<form id="owner-setup" method="post" action="/auth/setup">'
                   '<label>Setup code <input name="code" type="password" autocomplete="off" '
                   'minlength="44" maxlength="64" required></label><button type="submit">Sign in</button></form>')
    title = LOGIN_TITLE if button else "Ask your agent for a sign-in link"
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{CLAIM_THEME_META}
<title>{LOGIN_TITLE}</title>
<script src="/assets/theme.js"></script><link rel="stylesheet" href="/assets/claim.css"></head>
<body><main data-next="{html.escape(next_path)}"{" hidden" if signed_in else ""}>{CLAIM_BRAND}<h1>{title}</h1>
<p id="login-error" role="status">{messages.get(error, "")}</p>{button}
</main><script>{LOGIN_JS}</script></body></html>""", headers={"Content-Security-Policy": script_policy(LOGIN_JS)})


def _row(label: str, hint: str, value: str) -> str:
    return (f'<div class="setting"><div class="setting-label"><span>{html.escape(label)}</span><span class="setting-hint">{html.escape(hint)}</span></div>'
            f'<div class="setting-value"><span>{html.escape(value)}</span></div></div>')


def connect_code_page(code: dict[str, Any], public_url: str) -> HTMLResponse:
    cmd = f"curl -s -X POST {public_url}/connect -H 'content-type: application/json' -d '{{\"code\": \"{code['code']}\"}}'"
    body = (hero(PAIRING_TITLE, "")
            + f'<div class="card fade-in">{_row("Agent", "", code["agent"])}'
            + (_row("Project", "", code["project"]) if code.get("project") else "")
            + _row("Code", "Expires in 10 min", code["code"])
            + _row("Redeem", "", cmd) + "</div>"
            + '<p class="section-gap"><a href="/tokens" class="text-button">Back</a></p>')
    return page(PAIRING_TITLE, body, 200)


def _claim_document(title: str, body: str, status: int = 200, *, field_assets: bool = False) -> HTMLResponse:
    form_assets = ('<link rel="stylesheet" href="/claim-assets/claim.css">'
                   '<script src="/claim-assets/claim.js" defer></script>') if field_assets else ""
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet, noimageindex">
<meta name="referrer" content="same-origin">
{CLAIM_THEME_META}
<title>{html.escape(title)} | SwB</title>
<script src="/assets/theme.js"></script>
<link rel="stylesheet" href="/assets/claim.css">
{form_assets}
</head><body><main>
{CLAIM_BRAND}
{body}
</main></body></html>"""
    return HTMLResponse(doc, status_code=status, headers={
        "Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    })


def claim_message_page(title: str, status: int) -> HTMLResponse:
    return _claim_document(title, f'<h1>{html.escape(title)}</h1>', status)


def claim_page(title: str, scope: str, csrf: str, *, owner_key: bool = False, requester: str = "",
               credential: str = "Token", guide: bool = False, host: str = "", expires_at: float | None = None,
               now: float | None = None, kind: str = "token", typed: bool = False) -> HTMLResponse:
    from .claims import FILE_KINDS, Field

    field_assets = kind == "id" or kind in FILE_KINDS
    if field_assets:
        from .credentials import claim_field

        field = claim_field(Field("token", credential, kind))
    else:
        field = (f'<label for="token">{html.escape(credential)}</label>'
                 '<input id="token" name="token" type="password" required autocomplete="new-password" autocapitalize="none" spellcheck="false">')
    save = "Save" if typed else f"Save {html.escape(credential.lower())}"
    key_field = ('<label for="owner-key">SwB owner key</label>'
                 '<input id="owner-key" name="owner_key" type="password" required autocomplete="new-password" '
                 'autocapitalize="none" spellcheck="false">') if owner_key else ""
    connector = requester.lower().replace("_", "-").partition(":")[0].partition("-")[0]
    if connector in {"openai", "dot"}:
        connector = "chatgpt"
    avatar = agent_avatar_url(requester)
    mark_kind = avatar.removeprefix("/avatars/").removesuffix(".png") if avatar else connector if connector in CLAIM_CONNECTORS else "agent"
    mark = (f'<img class="persona-mark" src="{avatar}" alt="" width="22" height="22">' if avatar else
            f'<svg viewBox="0 0 24 24" aria-hidden="true"><use href="/assets/claim-connectors.svg#{mark_kind}"/></svg>')
    name = html.escape(requester)
    tile = (f'<div class="requester"><span class="requester-tile" data-connector="{mark_kind}" role="img" '
            f'aria-label="{name}" title="{name}">{mark}</span></div>') if requester else ""
    context = f'<p class="scope">{html.escape(scope)}</p>' if scope else ""
    help_link = ('<a class="guide" href="https://claude.ai/code" rel="noreferrer">Open Claude Code'
                 '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 5h6v6m0-6-9 9m6-1v6H5V8h6"/></svg></a>') if guide else ""
    expiry = (f"Expires in {max(1, math.ceil((expires_at - now) / 60))} min"
              if expires_at is not None and now is not None else "")
    footer = (f'<footer><span>{html.escape(host)}</span><span>{expiry}</span></footer>'
              if host or expiry else "")
    return _claim_document(title, f"""{tile}<h1>{html.escape(title)}</h1>{context}
<form method="post" autocomplete="off">
<input type="hidden" name="csrf" value="{html.escape(csrf)}">
{key_field}
{field}
{help_link}
<div class="actions">
<button type="submit"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>{save}</button>
</div>
</form>{footer}""", field_assets=field_assets)


def token_claim_page(name: str, csrf: str, *, kind: str = "token", label: str = "", require_https: bool = False,
                     owner_key: bool = False, host: str = "", expires_at: float | None = None,
                     now: float | None = None) -> HTMLResponse:
    credential = label or ("Webhook URL" if require_https or kind == "url" else "Token")
    connector = name.partition("_")[0].lower()
    requester, legacy_title = CLAIM_CONNECTORS.get("chatgpt" if connector == "openai" else connector, ("", ""))
    title = label or legacy_title or ("Save a webhook URL" if credential == "Webhook URL" else TOKEN_CLAIM_TITLE)
    return claim_page(title, f"For {name}.", csrf, requester=requester, credential=credential, owner_key=owner_key,
                      host=host, expires_at=expires_at, now=now, kind=kind, typed=bool(label))


def routine_claim_page(c: dict[str, Any], csrf: str, *, host: str = "", now: float | None = None) -> HTMLResponse:
    project = c["project"]
    scope = f"For {project}"
    if c["title"]:
        scope += f" in {c['title']}"
    scope += f" ({c['routine_id']})."
    requester = c["created_by"].partition(":")[2] or c["created_by"]
    return claim_page(ROUTINE_CLAIM_TITLE, scope, csrf, requester=requester, guide=True,
                      host=host, expires_at=c["expires_at"], now=now)


async def asset(request: Request) -> Response:
    hit = _ASSETS.get(request.path_params["name"])
    if hit is None:
        return Response(status_code=404)
    return Response(hit[1], media_type=hit[0], headers={"Cache-Control": "public, max-age=3600"})


def routes() -> list[Route]:
    return [Route("/assets/{name}", asset, methods=["GET"])]
