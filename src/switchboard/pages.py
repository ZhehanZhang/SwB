"""Server-rendered Switchboard pages."""

from __future__ import annotations

import base64
import hashlib
import html
import json
from datetime import datetime, timezone
from importlib import resources
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

_STATIC = resources.files("switchboard") / "static"
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


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    year = datetime.now(timezone.utc).year
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#f5f5f4" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#1c1917" media="(prefers-color-scheme: dark)">
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet, noimageindex">
<meta name="referrer" content="no-referrer">
<title>{html.escape(title)} | SwB</title>
<script src="/assets/prefs.js"></script>
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
    return HTMLResponse(doc, status_code=status, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Content-Security-Policy": CSP, "Referrer-Policy": "no-referrer"})


def hero(title: str, line: str = "") -> str:
    return f'<section class="hero fade-in"><h1>{html.escape(title)}</h1>{f"<p>{line}</p>" if line else ""}</section>'


def notice(text: str, tone: str = "bad") -> str:
    name = {"good": "checkCircle", "bad": "errorFill", "warn": "warningFill"}[tone]
    return f'<p class="notice" role="status">{icon(name, "tone-" + tone)}<span>{html.escape(text)}</span></p>'


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
const fragment = new URLSearchParams(location.hash.slice(1));
const next = () => location.replace(screen.dataset.next);
if (button) button.addEventListener("click", async () => {
  button.disabled = true;
  message.textContent = "";
  try { await passkey("login"); next(); }
  catch { message.textContent = "Passkey sign-in failed."; }
  finally { button.disabled = false; }
});
if (fragment.has("t")) {
  history.replaceState(null, "", location.pathname + location.search);
  if (button) button.disabled = true;
  post("/auth/link", {token: fragment.get("t")}).then(next).catch(() => {
    message.textContent = "This sign-in link is invalid or expired.";
    screen.hidden = false;
    if (button) button.disabled = false;
  });
} else if (screen.hidden) {
  next();
}
"""

REGISTER_JS = PASSKEY_JS + """
const add = document.querySelector("#add-passkey");
add.addEventListener("click", async () => {
  add.disabled = true;
  try { await passkey("register", add.dataset.csrf); location.reload(); }
  catch { document.querySelector("#passkey-error").textContent = "Couldn't add the passkey."; }
  finally { add.disabled = false; }
});
"""


def script_policy(script: str) -> str:
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    return CSP.replace("script-src 'self'", f"script-src 'self' 'sha256-{digest}'; connect-src 'self'")


def login_page(methods: dict[str, Any], next_path: str, error: str | None, *, signed_in: bool = False) -> HTMLResponse:
    messages = {"link_expired": "This sign-in link is invalid or expired.", "passkey_failed": "Passkey sign-in failed.",
                "oidc_failed": "Sign-in with the identity provider failed."}
    button = '<button type="button" id="passkey-login">Sign in with a passkey</button>' if methods["passkeys"] else ""
    oidc = methods["oidc"]
    if oidc:
        href = "/auth/oidc/start?" + urlencode({"next": next_path})
        button += f'<p><a href="{html.escape(href)}">Continue with {html.escape(oidc["name"])}</a></p>'
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>Sign in to SwB</title></head>
<body><main data-next="{html.escape(next_path)}"{" hidden" if signed_in else ""}><h1>Sign in to SwB</h1>
<p id="login-error" role="status">{messages.get(error, "")}</p>{button or "<p>Ask your agent for a sign-in link.</p>"}
</main><script>{LOGIN_JS}</script></body></html>""", headers={"Content-Security-Policy": script_policy(LOGIN_JS)})


def _row(label: str, hint: str, value: str, on: bool | None = None) -> str:
    mark = "" if on is None else (icon("checkCircle", "tone-good") if on else icon("warningFill", "tone-warn"))
    return (f'<div class="setting"><div class="setting-label"><span>{html.escape(label)}</span><span class="setting-hint">{html.escape(hint)}</span></div>'
            f'<div class="setting-value">{mark}<span>{html.escape(value)}</span></div></div>')


def _persona_row(p: dict[str, Any], csrf: str) -> str:
    pic = (f'<img src="{html.escape(p["url"])}" alt="" width="36" height="36" style="border-radius:8px;object-fit:cover">' if p.get("url")
           else html.escape(p.get("emoji") or ""))
    hint = ", ".join([p["id"], f"picture: {p['source']}" + (f" ({p['avatar']})" if p.get("avatar") else ""), "used by " + (", ".join(p["used_by"]) or "nobody by default")])
    form = ""
    if p.get("avatar"):
        accept = "image/png" if p["avatar"].endswith(".png") else ("image/gif" if p["avatar"].endswith(".gif") else "image/jpeg")
        form = (f'<form method="post" action="/slack/personas/{html.escape(p["id"])}/avatar" enctype="multipart/form-data">'
                f'<input type="hidden" name="csrf" value="{csrf}"><input type="file" name="file" accept="{accept}" required>'
                '<button class="text-button">Upload</button></form>')
    return (f'<div class="setting"><div class="setting-label"><span>{html.escape(p["name"])}</span><span class="setting-hint">{html.escape(hint)}</span></div>'
            f'<div class="setting-value">{pic}{form}</div></div>')


def slack_body(slack: dict[str, Any], pending: list[dict[str, Any]], csrf: str, notice_text: str | None, personas: list[dict[str, Any]] | None = None) -> str:
    """Slack workspaces and the write requests waiting for the owner, each with Approve and send / Reject."""
    ws_rows = "".join(
        _row(label, f"{w['intake']}" + (", user only" if w["user_only"] else "") + f", {w['watched']} watched" + (f", last event {w['last_event_at']}" if w.get("last_event_at") else "")
             + (f", missing {', '.join(w['missing_env'])}" if w["missing_env"] else "") + (f", error: {w['errors'][-1]}" if w["errors"] else ""),
             ", ".join(w["identities"]) or "no tokens", bool(w["identities"]) and not w["missing_env"] and (w["connected"] or w["intake"] != "socket"))
        for label, w in sorted(slack.get("workspaces", {}).items())
    ) or _row("No workspaces", "Add them under slack.workspaces in the config", "")

    def item(r: dict[str, Any]) -> str:
        p = r["params"]
        what = "Post" if r["action"] == "post_message" else f"React :{p.get('name')}:"
        facts = [r["id"], f"from {r['key_id']}", f"as {r['identity']} in {r['workspace']}", f"to {p.get('channel')}"]
        if r.get("persona"):
            facts.append(f"persona {r['persona']['name']}")
        if p.get("thread_ts"):
            facts.append(f"thread {p['thread_ts']}")
        hidden = f'<input type="hidden" name="csrf" value="{csrf}">'
        rid = html.escape(r["id"])
        return (f'<div class="setting"><div class="setting-label"><span>{html.escape(what)}</span><span class="setting-hint">{html.escape(", ".join(facts))}</span>'
                + (f'<span class="setting-hint">{html.escape(p["text"])}</span>' if p.get("text") else "")
                + f'</div><div class="setting-value"><form method="post" action="/slack/requests/{rid}/approve">{hidden}<button class="pill-button primary">Approve and send</button></form>'
                f'<form method="post" action="/slack/requests/{rid}/reject">{hidden}<button class="text-button">Reject</button></form></div></div>')

    pending_rows = "".join(item(r) for r in pending) or _row("Nothing waiting", "Writes that need your approval appear here and as a Slack DM", "")
    return (f'<section class="hero section-gap fade-in"><p>Slack workspaces</p></section><div class="card fade-in">{ws_rows}</div>'
            + (notice(notice_text, "good") if notice_text else "")
            + f'<section class="hero section-gap fade-in"><p>Slack writes waiting for you. Agents can never approve their own.</p></section><div class="card fade-in">{pending_rows}</div>'
            + (f'<section class="hero section-gap fade-in"><p>Personas: the name and picture on bot posts. An upload replaces the picture for new posts; '
               f'the Slack profile behind the name stays the app’s.</p></section><div class="card fade-in">{"".join(_persona_row(p, csrf) for p in personas)}</div>' if personas else ""))


def admin_page(health: dict[str, Any], actor: str, routes: list[dict[str, Any]], projects: list[dict[str, Any]], changes: list[dict[str, Any]],
               extra: str = "", block_rows: list[tuple[str, str, str, bool | None]] | None = None, connected: list[dict[str, Any]] | None = None,
               csrf: str = "", visibility: list[dict[str, Any]] | None = None, routines: list[dict[str, Any]] | None = None,
               systems: list[dict[str, Any]] | None = None, add_passkey: bool = False) -> HTMLResponse:
    ok = health["status"] == "ok"
    items = ""
    for name, b in health.get("blocks", {}).items():
        level = ", ".join(x for x in (f"out {b['level']['out']}" if b["level"]["out"] else "", f"in {b['level']['in']}" if b["level"]["in"] else "") if x)
        items += _row(f"Block: {name}", f"{b['use']} block, {b['kind']}" + (f", {level}" if level else ""), "On" if b["online"] else "Off", b["online"])
    items += _row("Memory mirror", "Markdown copy of every memory, one folder per scope", "On" if health["mirror"] else "Off", health["mirror"])
    items += _row("Bearer keys", "Agents that sign in with a key instead of OAuth", ", ".join(health["static_keys"]) or "none")
    items += "".join(_row(a, b, c, d) for a, b, c, d in block_rows or [])
    problems = "".join(notice(p, "warn") for p in health["problems"])
    status = (hero("SwB", ("Running" if ok else "Running, with parts switched off") + f". Signed in as {html.escape(actor)}.")
              + f'<div class="card fade-in">{items}</div>{problems}')
    body = (status + _systems_body(systems or []) + (_routines_body(routines, projects, csrf) if routines is not None else "") + _visibility_body(visibility or [], csrf)
            + _connect_body(connected or [], csrf) + extra + _routes_body(routes, projects, changes))
    if add_passkey:
        body = (f'<button type="button" id="add-passkey" data-csrf="{csrf}">Add a passkey</button>'
                f'<p id="passkey-error" role="status"></p>' + body + f"<script>{REGISTER_JS}</script>")
    response = page("SwB", body, 200)
    if add_passkey:
        response.headers["Content-Security-Policy"] = script_policy(REGISTER_JS)
    return response


def _systems_body(systems: list[dict[str, Any]]) -> str:
    """Each switchboard project and what it maps to in every system: the Captain its route reaches, Claude routines, links."""
    if not systems:
        return ""
    rows = "".join(_row(s["name"], f"project {s['slug']}", ", ".join(f"{k}: {v}" for k, v in s["systems"].items()) or "nothing mapped yet")
                   for s in systems)
    return (f'<section class="hero section-gap fade-in"><p>Projects across systems: one switchboard project and its counterpart in Capy, '
            f'Claude and the rest. A send to the project picks one by its routes.</p></section><div class="card fade-in">{rows}</div>')


def _routines_body(routines: list[dict[str, Any]], projects: list[dict[str, Any]], csrf: str) -> str:
    """Claude routines with their status, and a form to add one from its fire URL and token (shown once, never echoed)."""
    hidden = f'<input type="hidden" name="csrf" value="{csrf}">'

    def row(r: dict[str, Any]) -> str:
        hint = ", ".join(x for x in (r["kind"], f"project {r['project']}" if r["project"] else "", r["routine_id"], f"slugs {', '.join(r['slugs'])}",
                                      "from the config" if r["source"] == "config" else "") if x)
        control = (f'<form method="post" action="/routines/{html.escape(r["name"])}/remove">{hidden}<button class="text-button">Remove</button></form>'
                   if r["source"] == "stored" else "")
        return (f'<div class="setting"><div class="setting-label"><span>{html.escape(r["name"])}</span><span class="setting-hint">{html.escape(hint)}</span></div>'
                f'<div class="setting-value"><span class="setting-hint">{html.escape(r["status"])}</span>{control}</div></div>')

    options = '<option value="">general (no project)</option>' + "".join(
        f'<option value="{html.escape(p["slug"])}">{html.escape(p["name"])}</option>' for p in projects if not p.get("archived"))
    add = (f'<div class="setting"><div class="setting-label"><span>Add a routine</span><span class="setting-hint">In the Claude Project, create a task with '
           f'an API trigger, then paste its fire URL and token here. The token is stored encrypted and never shown again.</span></div>'
           f'<div class="setting-value"><form method="post" action="/routines" autocomplete="off">{hidden}'
           '<input name="fire_url" placeholder="https://api.anthropic.com/v1/claude_code/routines/trig_…/fire" required aria-label="Fire URL">'
           '<input name="token" type="password" placeholder="sk-ant-oat01-…" required aria-label="Token" autocomplete="new-password">'
           f'<select name="project" aria-label="Project">{options}</select>'
           '<button class="pill-button primary">Add</button></form></div></div>')
    rows = "".join(row(r) for r in routines) or _row("No routines yet", "Add one below", "")
    return (f'<section class="hero section-gap fade-in"><p>Claude routines: a send to claude (or claude:&lt;project&gt;) starts one. Untested until its first fire.</p></section>'
            f'<div class="card fade-in">{rows}{add}</div>')


def _visibility_body(rows: list[dict[str, Any]], csrf: str) -> str:
    """Each resource's private/shared pill; one click flips it. Tools are always private and show no button."""
    if not rows:
        return ""
    hidden = f'<input type="hidden" name="csrf" value="{csrf}">'

    def row(r: dict[str, Any]) -> str:
        vis = r["visibility"]
        label = "Shared" if vis == "shared" else "Private" if vis == "private" else "Shared with " + ", ".join(vis)
        hint = {"override": "set here", "config": "from the config", "default": "the default"}[r["source"]]
        if r["fixed"]:
            control = '<span class="setting-hint">Private, always</span>'
        else:
            flip = "private" if vis != "private" else "shared"
            control = (f'<form method="post" action="/visibility">{hidden}<input type="hidden" name="resource" value="{html.escape(r["resource"])}">'
                       f'<input type="hidden" name="visibility" value="{flip}"><button class="pill-button{" primary" if vis != "private" else ""}" '
                       f'title="Make it {flip}">{html.escape(label)}</button></form>')
        return (f'<div class="setting"><div class="setting-label"><span>{html.escape(r["resource"])}</span><span class="setting-hint">{hint}</span></div>'
                f'<div class="setting-value">{control}</div></div>')

    return (f'<section class="hero section-gap fade-in"><p>Who can reach what. Private: only you and your own agents. Shared: anyone in that workspace. '
            f'Anyone else’s message to something private lands in its inbox as context.</p></section><div class="card fade-in">{"".join(row(r) for r in rows)}</div>')


def _connect_body(keys: list[dict[str, Any]], csrf: str) -> str:
    """Agents connected with a pairing code, each revocable, and a form that mints a new code."""
    hidden = f'<input type="hidden" name="csrf" value="{csrf}">'

    def row(k: dict[str, Any]) -> str:
        used = _ago(k.get("last_used_at"))
        hint = ", ".join(x for x in (k["id"], f"project {k['project']}" if k.get("project") else "", k.get("client") or "", f"last call {used}" if used else "never called") if x)
        return (f'<div class="setting"><div class="setting-label"><span>{html.escape(k["agent"])}</span><span class="setting-hint">{html.escape(hint)}</span></div>'
                f'<div class="setting-value"><form method="post" action="/pairing/keys/{html.escape(k["id"])}/revoke">{hidden}<button class="text-button">Revoke</button></form></div></div>')

    rows = "".join(row(k) for k in keys) or _row("Nobody connected yet", "Mint a pairing code below, then redeem it on the machine the agent runs on", "")
    mint = (f'<div class="setting"><div class="setting-label"><span>Connect a new agent</span><span class="setting-hint">A one-time code, valid 10 minutes. '
            f'The client redeems it for its own key.</span></div><div class="setting-value"><form method="post" action="/pairing/code">{hidden}'
            '<input name="agent" placeholder="agent, e.g. codex" required pattern="[a-z0-9][a-z0-9-]{0,39}" aria-label="Agent name">'
            '<input name="project" placeholder="project (optional)" aria-label="Project">'
            '<button class="pill-button primary">Mint code</button></form></div></div>')
    return (f'<section class="hero section-gap fade-in"><p>Connected agents: one key each, from a pairing code. Revoking stops the next call.</p></section>'
            f'<div class="card fade-in">{rows}{mint}</div>')


def _ago(at: float | None) -> str:
    if not at:
        return ""
    return datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def connect_code_page(code: dict[str, Any], public_url: str) -> HTMLResponse:
    cmd = f"curl -s -X POST {public_url}/connect -H 'content-type: application/json' -d '{{\"code\": \"{code['code']}\"}}'"
    body = (hero("Pairing code", f"For <strong>{html.escape(code['agent'])}</strong>" + (f" in project {html.escape(code['project'])}" if code.get("project") else "")
                 + ". It works once, for 10 minutes.")
            + f'<div class="card fade-in">{_row("Code", "Give it to the client that redeems it", code["code"])}'
            + _row("Redeem", "Run this on the machine the agent runs on; it prints the agent's key once, with the MCP URL", cmd) + "</div>"
            + '<p class="section-gap"><a href="/" class="text-button">Back</a></p>')
    return page("Pairing code", body, 200)


def claim_page(title: str, rows: str, csrf: str, *, intro: str = "", hint: str = "", placeholder: str = "", owner_key: bool = False) -> HTMLResponse:
    key_field = ('<input name="owner_key" type="password" placeholder="Switchboard owner key" required '
                 'aria-label="Switchboard owner key" autocomplete="new-password">' if owner_key else "")
    form = (f'<div class="setting"><div class="setting-label"><span>Token</span><span class="setting-hint">{html.escape(hint)}</span></div>'
            f'<div class="setting-value"><form method="post" autocomplete="off"><input type="hidden" name="csrf" value="{csrf}">{key_field}'
            f'<input name="token" type="password" placeholder="{html.escape(placeholder)}" required aria-label="Token" autocomplete="new-password">'
            '<button class="pill-button primary">Save</button></form></div></div>')
    return page(title, hero(title, html.escape(intro)) + f'<div class="card fade-in">{rows}{form}</div>')


def token_claim_page(name: str, csrf: str) -> HTMLResponse:
    return claim_page("Save a token", _row("Credential", "For Switchboard", name), csrf,
                      hint="Stored encrypted and never shown again.")


def routine_claim_page(c: dict[str, Any], csrf: str) -> HTMLResponse:
    np = c.get("new_project")
    rows = (_row("Routine", f"{c['routine_id']}" + (" (primary for the project)" if c["primary"] else ""), c["name"])
            + _row("Claude Project", "Where the routine lives", c["title"] or "-")
            + _row("Switchboard project", "Created when you save" if np else "Already exists", c["project"] + (f" ({np['name']})" if np else ""))
            + _row("Asked by", "The signed-in session that called routine_register", c["created_by"]))
    return claim_page("Register a Claude routine", rows, csrf, intro="Paste the routine's token and save.",
                      hint="From the routine's API trigger (Generate token). Stored encrypted, never shown again. This link works once.",
                      placeholder="sk-ant-oat01-…")


def _routes_body(routes: list[dict[str, Any]], projects: list[dict[str, Any]], changes: list[dict[str, Any]]) -> str:
    def target(r: dict[str, Any]) -> str:
        t = r["target"]
        kind = next((k for k, v in t.items() if v and k not in ("name", "captain_project", "project_id")), None)
        return f"{r['destination']} {kind} {t.get('name') or t[kind]}" if kind else "?"

    def who(r: dict[str, Any]) -> str:
        return f"{r['agent'] or 'any agent'} / {r['project'] or 'any project'}"

    route_rows = "".join(
        _row(who(r), f"{r['level']} route" + (", override allowed" if r["allow_override"] else "") + (f", {r['note']}" if r["note"] else "")
             + f", set by {r['updated_by']} {(r['updated_at'] or '')[:16]}", target(r) + (f" ({', '.join(f'{k}={v}' for k, v in r['defaults'].items())})" if r["defaults"] else ""))
        for r in routes
    ) or _row("No routes", "Set one with the routes_set tool, or routes: in the config", "")
    project_rows = "".join(
        _row(p["name"], f"{p['scope']}" + (", archived" if p["archived"] else ""), ", ".join(f"{k}: {v if isinstance(v, str) else ', '.join(v)}" for k, v in p["links"].items()) or "no links")
        for p in projects
    ) or _row("No projects", "Create one with the project_create tool", "")
    change_rows = "".join(_row(f"{c['action'].replace('_', ' ')} {c['target'] or ''}", f"{c['at']} by {c['source']} via {c['origin']}", c["outcome"] + (f": {c['detail']}" if c["detail"] else ""), c["outcome"] == "ok") for c in changes)
    return ('<section class="hero section-gap fade-in"><p>Routes: where send goes for each assistant when it names no target. Resolution: agent and project, then project, then agent, then global.</p></section>'
            + f'<div class="card fade-in">{route_rows}</div>'
            + f'<section class="hero section-gap fade-in"><p>Projects</p></section><div class="card fade-in">{project_rows}</div>'
            + (f'<section class="hero section-gap fade-in"><p>Recent route changes and refusals</p></section><div class="card fade-in">{change_rows}</div>' if changes else ""))


async def asset(request: Request) -> Response:
    hit = _ASSETS.get(request.path_params["name"])
    if hit is None:
        return Response(status_code=404)
    return Response(hit[1], media_type=hit[0], headers={"Cache-Control": "public, max-age=3600"})


def routes() -> list[Route]:
    return [Route("/assets/{name}", asset, methods=["GET"])]
