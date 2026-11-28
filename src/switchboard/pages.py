"""Server-rendered Switchboard pages."""

from __future__ import annotations

import base64
import hashlib
import html
import math
from importlib import resources
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

from . import USER_AGENT
from .slack.avatar_files import agent_avatar_url

_STATIC = resources.files("switchboard") / "static"
_ASSETS = {
    "claim.css": ("text/css; charset=utf-8", (_STATIC / "claim.css").read_bytes()),
    "console-tokens.css": ("text/css; charset=utf-8", (_STATIC / "console-tokens.css").read_bytes()),
    "theme.js": ("text/javascript; charset=utf-8", (_STATIC / "theme.js").read_bytes()),
    "claim-brand.svg": ("image/svg+xml", (_STATIC / "claim-brand.svg").read_bytes()),
    "claim-connectors.svg": ("image/svg+xml", (_STATIC / "claim-connectors.svg").read_bytes()),
    **{name: ("font/woff2", (_STATIC / "fonts" / name).read_bytes())
       for name in ("inter.woff2", "jetbrains-mono.woff2")},
}

CSP = "default-src 'none'; style-src 'self'; script-src 'self'; font-src 'self'; img-src 'self' data:; form-action 'self' https: http://localhost:* http://127.0.0.1:*; frame-ancestors 'none'; base-uri 'none'"
CLAIM_CSP = CSP.replace("img-src 'self' data:", "img-src 'self'").replace(
    "form-action 'self' https: http://localhost:* http://127.0.0.1:*", "form-action 'self'",
)
CLAIM_BRAND = '<img class="brand" src="/assets/claim-brand.svg" alt="SwB" width="56" height="56">'
THEME_META = (
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


def page(title: str, body: str = "", status: int = 200, *, secondary: str = "", mark: str = "",
         main_attrs: str = "", script: str = "", field_assets: bool = False, csp: str = CSP) -> HTMLResponse:
    form_assets = ('<link rel="stylesheet" href="/claim-assets/claim.css">'
                   '<script src="/claim-assets/claim.js" defer></script>') if field_assets else ""
    context = f'<p class="scope">{secondary}</p>' if secondary else ""
    inline_script = f"<script>{script}</script>" if script else ""
    tab_title = title if title.endswith(" SwB") else f"{title} | SwB"
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet, noimageindex">
<meta name="referrer" content="same-origin">
{THEME_META}
<title>{html.escape(tab_title)}</title>
<script src="/assets/theme.js"></script>
<link rel="stylesheet" href="/assets/console-tokens.css">
<link rel="stylesheet" href="/assets/claim.css">
{form_assets}
</head><body><main{main_attrs}>
<header><div class="marks">{CLAIM_BRAND}{mark}</div><h1>{html.escape(title)}</h1>{context}</header>
{body}</main>{inline_script}</body></html>"""
    return HTMLResponse(doc, status_code=status, headers={
        "Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
        "X-Content-Type-Options": "nosniff", "Content-Security-Policy": csp,
    })


def message_page(title: str, text: str, status: int) -> HTMLResponse:
    return page(title, status=status, secondary=html.escape(text))


def requester_mark(kind: str) -> str:
    avatar = agent_avatar_url(kind) or ("/avatars/capy.png" if kind == "capy" else None)
    connector = kind.lower().replace("_", "-").partition(":")[0].partition("-")[0]
    mark = (f'<img class="persona-mark" src="{avatar}" alt="" width="56" height="56">' if avatar else
            f'<svg viewBox="0 0 24 24"><use href="/assets/claim-connectors.svg#{connector if connector in CLAIM_CONNECTORS else "agent"}"/></svg>')
    return f'<span class="requester-tile" data-connector="{connector if connector in CLAIM_CONNECTORS else ""}" aria-hidden="true">{mark}</span>'


CONSENT_JS = """
const approve = document.querySelector('button[name="decision"][value="approve"]');
document.addEventListener("keydown", event => {
  if (event.target !== document.body || event.key !== "Enter" || event.defaultPrevented ||
      event.isComposing || event.repeat || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  event.preventDefault();
  approve.form.requestSubmit(approve);
});
"""


def authorize_page(name: str, host: str, account: str, agent: str, req: str, csrf: str,
                   scopes: tuple[str, ...], address: str = "") -> HTMLResponse:
    secondary = f'<span>Returns to <code>{html.escape(host)}</code></span> <span>Signed in as {html.escape(account)}</span>'
    if address:
        secondary += f' <span class="mailbox">Mailbox <code>{html.escape(address)}</code></span>'
    scope_icon = ('<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="8" cy="15" r="4"/>'
                  '<path d="m11 12 8-8m-3 3 3 3"/></svg>')
    rows = "".join(f"<li>{scope_icon}<code>{html.escape(scope)}</code></li>" for scope in scopes)
    body = f"""<form method="post">
<input type="hidden" name="req" value="{html.escape(req)}">
<input type="hidden" name="csrf" value="{html.escape(csrf)}">
{f'<ul class="scopes" aria-label="Requested scopes">{rows}</ul>' if rows else ""}
<div class="actions consent-actions">
<button type="submit" class="button primary" name="decision" value="approve">Approve</button>
<button type="submit" class="button secondary" name="decision" value="deny" formnovalidate>Deny</button>
</div>
</form>"""
    return page(f"Connect {name} to SwB", body, secondary=secondary, mark=requester_mark(agent),
                script=CONSENT_JS, csp=script_policy(CONSENT_JS))


PASSKEY_JS = """
async function post(path, body, csrf, signal) {
  const headers = {"Content-Type": "application/json"};
  if (csrf) headers["X-CSRF-Token"] = csrf;
  const response = await fetch(path, {method: "POST", headers, body: JSON.stringify(body), signal});
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error || "Request refused");
  }
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
  const {publicKey} = await post(path + "/options", {}, csrf, AbortSignal.timeout(15000));
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
  await post(path + "/verify", {credential: result, ...(register ? {name: "Passkey"} : {})}, csrf, AbortSignal.timeout(15000));
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
  post("/auth/link", {token: fragment.get("t")}).then(next).catch((error) => {
    const reasons = {
      link_invalid: "Check your sign-in link.",
      link_used: "Ask your agent for a new link.",
      link_expired: "Ask your agent for a new link."
    };
    message.textContent = reasons[error.message] || "Try signing in again.";
    screen.hidden = false;
    if (button) button.disabled = false;
  });
} else if (screen.hidden) {
  next();
}
"""

def script_policy(script: str, *, connect: bool = False) -> str:
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    policy = CSP.replace("script-src 'self'", f"script-src 'self' 'sha256-{digest}'")
    return policy + "; connect-src 'self'" if connect else policy


def login_page(methods: dict[str, Any], next_path: str, error: str | None, *, signed_in: bool = False) -> HTMLResponse:
    messages = {"link_invalid": "Check your sign-in link.",
                "link_expired": "Ask your agent for a new link.",
                "link_used": "Ask your agent for a new link.",
                "passkey_failed": "Try your passkey again.",
                "oidc_failed": "Try signing in again."}
    buttons = []
    if methods["passkeys"]:
        buttons.append('<button type="button" id="passkey-login" class="button primary">Sign in with a passkey</button>')
    oidc = methods["oidc"]
    if oidc:
        href = "/auth/oidc/start?" + urlencode({"next": next_path})
        style = "secondary" if buttons else "primary"
        buttons.append(f'<a class="button {style}" href="{html.escape(href)}">Continue with {html.escape(oidc["name"])}</a>')
    actions = f'<div class="actions">{"".join(buttons)}</div>' if buttons else ""
    if methods.get("setup"):
        actions += ('<form id="owner-setup" method="post" action="/auth/setup">'
                    '<label for="setup-code">Setup code</label>'
                    '<input id="setup-code" name="code" type="password" autocomplete="one-time-code" '
                    'minlength="44" maxlength="64" required>'
                    '<div class="actions"><button type="submit" class="button primary">Sign in</button></div></form>')
    title = LOGIN_TITLE if actions else "Ask your agent for a sign-in link"
    return page(title, f'<p id="login-error" role="status">{messages.get(error, "")}</p>{actions}',
                main_attrs=f' data-next="{html.escape(next_path)}"{" hidden" if signed_in else ""}',
                script=LOGIN_JS, csp=script_policy(LOGIN_JS, connect=True))


def connect_code_page(code: dict[str, Any], public_url: str) -> HTMLResponse:
    cmd = f"curl -s -A '{USER_AGENT}' -X POST {public_url}/connect -H 'content-type: application/json' -d '{{\"code\": \"{code['code']}\"}}'"
    project = f'<dt>Project</dt><dd>{html.escape(code["project"])}</dd>' if code.get("project") else ""
    body = f"""<dl class="pairing-details">
<dt>Agent</dt><dd>{html.escape(code["agent"])}</dd>{project}
<dt>Code</dt><dd><code>{html.escape(code["code"])}</code></dd>
<dt>Expires in</dt><dd>10 min</dd>
<dt>Redeem</dt><dd><code>{html.escape(cmd)}</code></dd>
</dl>
<div class="actions"><a href="/tokens" class="button secondary">Back</a></div>"""
    return page(PAIRING_TITLE, body)


def claim_message_page(title: str, status: int) -> HTMLResponse:
    return page(title, status=status, csp=CLAIM_CSP)


def claim_page(title: str, scope: str, csrf: str, *, owner_key: bool = False, requester: str = "",
               credential: str = "Token", guide: bool = False, host: str = "", expires_at: float | None = None,
               now: float | None = None, kind: str = "token", typed: bool = False, error: str = "") -> HTMLResponse:
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
    mark = requester_mark(requester) if requester else ""
    help_link = ('<a class="guide" href="https://claude.ai/code" rel="noreferrer">Open Claude Code'
                 '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 5h6v6m0-6-9 9m6-1v6H5V8h6"/></svg></a>') if guide else ""
    expiry = (f"Expires in {max(1, math.ceil((expires_at - now) / 60))} min"
              if expires_at is not None and now is not None else "")
    footer = (f'<footer><span>{html.escape(host)}</span><span>{expiry}</span></footer>'
              if host or expiry else "")
    heading = error or title
    body = f"""<form method="post" autocomplete="off">
<input type="hidden" name="csrf" value="{html.escape(csrf)}">
{key_field}
{field}
{help_link}
<div class="actions">
<button type="submit" class="button primary"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>{save}</button>
</div>
</form>{footer}"""
    return page(heading, body, 400 if error else 200, secondary=html.escape(scope), mark=mark,
                field_assets=field_assets, csp=CLAIM_CSP)


def token_claim_page(name: str, csrf: str, *, kind: str = "token", label: str = "", require_https: bool = False,
                     owner_key: bool = False, host: str = "", expires_at: float | None = None,
                     now: float | None = None, error: str = "") -> HTMLResponse:
    credential = label or ("Webhook URL" if require_https or kind == "url" else "Token")
    connector = name.partition("_")[0].lower()
    requester, legacy_title = CLAIM_CONNECTORS.get("chatgpt" if connector == "openai" else connector, ("", ""))
    title = label or legacy_title or ("Save a webhook URL" if credential == "Webhook URL" else TOKEN_CLAIM_TITLE)
    return claim_page(title, f"For {name}.", csrf, requester=requester, credential=credential, owner_key=owner_key,
                      host=host, expires_at=expires_at, now=now, kind=kind, typed=bool(label), error=error)


def routine_claim_page(c: dict[str, Any], csrf: str, *, host: str = "", now: float | None = None,
                       error: str = "") -> HTMLResponse:
    project = c["project"]
    scope = f"For {project}"
    if c["title"]:
        scope += f" in {c['title']}"
    scope += f" ({c['routine_id']})."
    requester = c["created_by"].partition(":")[2] or c["created_by"]
    return claim_page(ROUTINE_CLAIM_TITLE, scope, csrf, requester=requester, guide=True,
                      host=host, expires_at=c["expires_at"], now=now, error=error)


async def asset(request: Request) -> Response:
    hit = _ASSETS.get(request.path_params["name"])
    if hit is None:
        return Response(status_code=404)
    return Response(hit[1], media_type=hit[0], headers={"Cache-Control": "public, max-age=3600"})


def routes() -> list[Route]:
    return [Route("/assets/{name}", asset, methods=["GET"])]
