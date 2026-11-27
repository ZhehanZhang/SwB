"""Grouped credential claims: what a provider needs, its mark, and the one-link form that saves every field together.

A provider with several credentials (a GitHub App's ID and private key, a Slack workspace's bot, user and app tokens)
gets one link instead of one per field. Every field is checked before anything is stored, key fields also take a file
(.pem, .key, .txt, .json) read in the browser into the same form field, and the page shows the provider's mark and a
link to where each credential comes from.
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from pathlib import Path

from starlette.responses import HTMLResponse, Response

from .claims import FILE_KINDS, MAX_FILE_BYTES, Field

MARKS = Path(__file__).parent / "marks"
ASSET_PATH = "/claim-assets"


@dataclass(frozen=True)
class Provider:
    key: str
    title: str
    mark: str


PROVIDERS = {p.key: p for p in (
    Provider("github", "GitHub", "github"),
    Provider("slack", "Slack", "slack"),
    Provider("discord", "Discord", "discord"),
    Provider("telegram", "Telegram", "telegram"),
    Provider("chatgpt", "ChatGPT", "openai"),
    Provider("claude", "Claude", "claude"),
    Provider("gemini", "Gemini", "gemini"),
)}


def github_app(webhook_secret_env: str | None = None, *, webhook_secret_saved: bool = False) -> tuple[str, str, list[Field]]:
    """The GitHub App fields: its numeric ID and private key, plus the webhook secret while none is saved."""
    settings = "https://github.com/settings/apps"
    fields = [Field("GITHUB_APP_ID", "App ID", "id", link=settings),
              Field("GITHUB_APP_PRIVATE_KEY", "Private key", "pem", link=settings)]
    if webhook_secret_env and not webhook_secret_saved:
        fields.append(Field(webhook_secret_env, "Webhook secret", "token", optional=True, link=settings))
    return "GitHub credentials", "github", fields


def slack_workspace(label: str, names: dict[str, str]) -> tuple[str, str, list[Field]]:
    """A Slack workspace's tokens, keyed bot, user and app as in its identities and app_token_env."""
    apps = "https://api.slack.com/apps"
    text = {"bot": "Bot token", "user": "User token", "app": "App-level token"}
    fields = [Field(env, text[kind], "token", link=apps) for kind, env in names.items() if kind in text]
    return f"Slack tokens for {label}", "slack", fields


def requester(provider: str) -> str:
    p = PROVIDERS.get(provider)
    if p is None:
        return ""
    sprite = p.key if p.key in CONNECTORS else "agent"
    return (f'<p class="requester"><span class="requester-tile" data-connector="{sprite}" aria-hidden="true"><svg viewBox="0 0 24 24">'
            f'<use href="{ASSET_PATH}/connectors.svg#{sprite}"/></svg></span>{html.escape(p.title)} asks for</p>')


def _field(f: Field) -> str:
    fid = f"f-{f.name}"
    get = (f'<a class="get" href="{html.escape(f.link)}" target="_blank" rel="noreferrer">Where to get it'
           '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 5h6v6m0-6-9 9m6-1v6H5V8h6"/></svg></a>') if f.link else ""
    label = html.escape(f.label) + (' <span class="optional">optional</span>' if f.optional else "")
    required = "" if f.optional else " required"
    if f.kind in FILE_KINDS:
        accept = ".pem,.key,.txt" if f.kind == "pem" else ".json,.txt"
        control = (f'<textarea id="{fid}" name="{f.name}" rows="4" spellcheck="false" autocomplete="off" autocapitalize="none"{required}></textarea>'
                   f'<div class="upload"><label class="file"><input type="file" accept="{accept}" data-target="{fid}" data-max="{MAX_FILE_BYTES}">'
                   '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 16V4m0 0-5 5m5-5 5 5M5 20h14"/></svg>'
                   f'Upload {".pem" if f.kind == "pem" else ".json"} file</label><span class="file-name" data-for="{fid}"></span></div>')
    else:
        kind = ' inputmode="numeric" type="text"' if f.kind == "id" else ' type="password"'
        control = f'<input id="{fid}" name="{f.name}"{kind} autocomplete="off" autocapitalize="none" spellcheck="false"{required}>'
    return f'<div class="field"><div class="field-head"><label for="{fid}">{label}</label>{get}</div>{control}</div>'


def page(group: dict, csrf: str, *, owner_key: bool = False, host: str = "", now: float | None = None) -> HTMLResponse:
    fields = group["fields"]
    required = [f for f in fields if not f.optional]
    key = ('<label for="owner-key">SwB owner key</label><input id="owner-key" name="owner_key" type="password" required '
           'autocomplete="new-password" autocapitalize="none" spellcheck="false">') if owner_key else ""
    expiry = f"Link expires in {max(1, math.ceil((group['expires_at'] - now) / 60))} min" if now is not None else ""
    footer = f'<footer><span>{html.escape(host)}</span><span>{expiry}</span></footer>' if host or expiry else ""
    save = "Save both" if len(required) == 2 and len(fields) == 2 else "Save all" if len(fields) > 1 else "Save"
    body = f"""{requester(group["provider"])}<h1>{html.escape(group["title"])}</h1>
<form method="post" autocomplete="off">
<input type="hidden" name="csrf" value="{html.escape(csrf)}">
{key}{"".join(_field(f) for f in fields)}
<div class="actions">
<button type="submit"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>{save}</button>
</div>
</form>{footer}"""
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet, noimageindex">
<meta name="referrer" content="no-referrer">
<title>{html.escape(group["title"])} | SwB</title>
<link rel="stylesheet" href="{ASSET_PATH}/claim.css">
</head><body><main>
<img class="brand" src="{ASSET_PATH}/swb.svg" alt="SwB" width="56" height="56">
{body}
</main><script src="{ASSET_PATH}/claim.js"></script></body></html>"""
    return HTMLResponse(doc, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                                      "X-Content-Type-Options": "nosniff",
                                      "Content-Security-Policy": "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; "
                                                                 "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})


CONNECTORS = set(re.findall(r'<symbol id="([a-z-]+)"', (MARKS / "connectors.svg").read_text()))

CSS = (MARKS / "claim.css").read_text()

JS = """document.querySelectorAll('input[type=file][data-target]').forEach(function (input) {
  input.addEventListener('change', function () {
    var area = document.getElementById(input.dataset.target);
    var note = document.querySelector('.file-name[data-for="' + input.dataset.target + '"]');
    var file = input.files && input.files[0];
    if (!file) return;
    note.classList.remove('error');
    if (file.size > Number(input.dataset.max)) {
      note.textContent = 'Choose a file under 64 KB'; note.classList.add('error'); input.value = ''; return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      area.value = String(reader.result); area.classList.add('loaded'); input.value = '';
      note.textContent = file.name;
    };
    reader.onerror = function () { note.textContent = 'Choose another file'; note.classList.add('error'); };
    reader.readAsText(file);
  });
});
"""


def asset(name: str) -> Response:
    if name == "claim.css":
        return Response(CSS, media_type="text/css; charset=utf-8", headers={"Cache-Control": "public, max-age=3600"})
    if name == "claim.js":
        return Response(JS, media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "public, max-age=3600"})
    if name in ("swb.svg", "connectors.svg"):
        return Response((MARKS / name).read_bytes(), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})
    if (p := PROVIDERS.get(name.removesuffix(".svg"))) and (MARKS / f"{p.mark}.svg").exists():
        return Response((MARKS / f"{p.mark}.svg").read_bytes(), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})
    return Response(status_code=404)
