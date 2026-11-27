"""Grouped credential claims: what a provider needs, its mark, and the one-link form that saves every field together.

A provider with several credentials (a GitHub App's ID and private key, a Slack workspace's bot, user and app tokens)
gets one link instead of one per field. Every field is checked before anything is stored, key fields also take a file
(.pem, .key, .txt, .json) read in the browser into the same form field, and the page shows the provider's mark and a
link to where each credential comes from.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass
from pathlib import Path

from starlette.responses import HTMLResponse, Response

from . import pages
from .claims import FILE_KINDS, MAX_FILE_BYTES, Field

MARKS = Path(__file__).parent / "marks"


@dataclass(frozen=True)
class Provider:
    key: str
    title: str
    mark: str
    light: str
    dark: str


PROVIDERS = {p.key: p for p in (
    Provider("github", "GitHub", "github", "#18181b", "#f4f4f5"),
    Provider("slack", "Slack", "slack", "#4a154b", "#e9d8ea"),
    Provider("discord", "Discord", "discord", "#5865f2", "#5865f2"),
    Provider("telegram", "Telegram", "telegram", "#229ed9", "#229ed9"),
    Provider("chatgpt", "ChatGPT", "openai", "#0d0d0d", "#f4f4f5"),
    Provider("claude", "Claude", "claude", "#d97757", "#d97757"),
    Provider("gemini", "Gemini", "gemini", "#8e75b2", "#8e75b2"),
)}


def github_app(webhook_secret_env: str | None = None, *, webhook_secret_saved: bool = False) -> tuple[str, str, list[Field]]:
    """The GitHub App fields: its numeric ID and private key, plus the webhook secret while none is saved."""
    settings = "https://github.com/settings/apps"
    fields = [Field("GITHUB_APP_ID", "App ID", "id", "The number at the top of the app's General settings.", link=settings),
              Field("GITHUB_APP_PRIVATE_KEY", "Private key", "pem",
                    "Under Private keys, Generate a private key, then upload the .pem file it downloads.", link=settings)]
    if webhook_secret_env and not webhook_secret_saved:
        fields.append(Field(webhook_secret_env, "Webhook secret", "token", "The secret set under Webhook, if the app sends events.",
                            optional=True, link=settings))
    return "GitHub App", "github", fields


def slack_workspace(label: str, names: dict[str, str]) -> tuple[str, str, list[Field]]:
    """A Slack workspace's tokens, keyed bot, user and app as in its identities and app_token_env."""
    apps = "https://api.slack.com/apps"
    text = {"bot": ("Bot token", "OAuth & Permissions, Bot User OAuth Token (xoxb-)."),
            "user": ("User token", "OAuth & Permissions, User OAuth Token (xoxp-)."),
            "app": ("App-level token", "Basic Information, App-Level Tokens, with connections:write (xapp-).")}
    fields = [Field(env, text[kind][0], "token", text[kind][1], link=apps) for kind, env in names.items() if kind in text]
    return f"Slack workspace {label}", "slack", fields


def mark(provider: str) -> str:
    p = PROVIDERS.get(provider)
    path = MARKS / f"{p.mark}.svg" if p else None
    if path is None or not path.exists():
        return ""
    svg = path.read_text().strip().replace("<svg ", f'<svg class="mark mark-{p.key}" aria-hidden="true" ', 1)
    return f'<span class="requester-tile" data-connector="{p.key}" title="{html.escape(p.title)}">{svg}</span>'


def _field(f: Field) -> str:
    fid = f"f-{f.name}"
    get = (f'<a class="get" href="{html.escape(f.link)}" target="_blank" rel="noreferrer">Provider settings'
           '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M13 5h6v6m0-6-9 9m6-1v6H5V8h6"/></svg></a>') if f.link else ""
    label = html.escape(f.label) + (' <span class="optional">optional</span>' if f.optional else "")
    required = "" if f.optional else " required"
    if f.kind in FILE_KINDS:
        accept = ".pem,.key,.txt" if f.kind == "pem" else ".json,.txt"
        placeholder = "-----BEGIN PRIVATE KEY-----" if f.kind == "pem" else "{ ... }"
        control = (f'<textarea id="{fid}" name="{f.name}" rows="4" spellcheck="false" autocomplete="off" autocapitalize="none"'
                   f' placeholder="{placeholder}"{required}></textarea>'
                   f'<div class="upload"><label class="file"><input type="file" accept="{accept}" data-target="{fid}" data-max="{MAX_FILE_BYTES}">'
                   '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 16V4m0 0-5 5m5-5 5 5M5 20h14"/></svg>'
                   f'Upload {".pem" if f.kind == "pem" else ".json"} file</label><span class="file-name" data-for="{fid}"></span></div>')
    else:
        kind = ' inputmode="numeric" type="text"' if f.kind == "id" else ' type="password"'
        control = f'<input id="{fid}" name="{f.name}"{kind} autocomplete="off" autocapitalize="none" spellcheck="false"{required}>'
    return f'<div class="field"><div class="field-head"><label for="{fid}">{label}</label>{get}</div>{control}</div>'


def page(group: dict, csrf: str, *, owner_key: bool = False, host: str = "", now: float | None = None) -> HTMLResponse:
    fields = group["fields"]
    key = ('<div class="field"><label for="owner-key">SwB owner key</label><input id="owner-key" name="owner_key" type="password" required '
           'autocomplete="new-password" autocapitalize="none" spellcheck="false"></div>') if owner_key else ""
    expiry = f"Expires in {max(1, math.ceil((group['expires_at'] - now) / 60))} min" if now is not None else ""
    footer = f'<footer><span>{html.escape(host)}</span><span>{expiry}</span></footer>' if host or expiry else ""
    body = f"""{mark(group["provider"])}<h1>Save credentials</h1>
<p class="scope">For {html.escape(group["title"])}.</p>
<form method="post" autocomplete="off">
<input type="hidden" name="csrf" value="{html.escape(csrf)}">
{key}{"".join(_field(f) for f in fields)}
<div class="actions"><button type="submit"><svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>Save</button></div>
</form>{footer}"""
    return pages._claim_document("Save credentials", body, grouped=True)


def _marks_css() -> str:
    return "\n".join(f".mark-{p.key} {{ color: light-dark({p.light}, {p.dark}); }}" for p in PROVIDERS.values())


CSS = """.requester-tile { margin-bottom: 8px; }
.mark { fill: currentColor; stroke: none; }
.field { margin-bottom: 20px; }
.field-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 4px; }
.field-head label { margin: 0; }
.optional { color: var(--muted); font-weight: 400; }
.get { display: inline-flex; align-items: center; gap: 4px; color: var(--muted); text-decoration: none; font-size: 12px; }
.get:hover { color: var(--foreground); text-decoration: underline; }
input[type="text"], textarea { display: block; width: 100%; min-height: 36px; margin: 0; padding: 8px 12px;
  border: 1px solid var(--border-strong); border-radius: 6px; color: inherit; background: var(--background); font: 13px ui-monospace, monospace; }
textarea { resize: vertical; min-height: 84px; }
textarea.loaded { display: none; }
.upload { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; margin-top: 8px; }
.file { display: inline-flex; align-items: center; gap: 6px; padding: 5px 12px; border: 1px solid var(--border-strong); border-radius: 6px; cursor: pointer; font-weight: 500; }
.file:hover { border-color: var(--muted); }
.file input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.file:focus-within { outline: 2px solid var(--accent); outline-offset: 2px; }
.file-name { color: var(--muted); font-size: 12px; }
.file-name.error { color: light-dark(#c8372d, #e5675c); }
.get .icon { width: 13px; height: 13px; }
@media (max-width: 560px) { input[type="text"], textarea { font-size: 16px; } }
""" + _marks_css()

JS = """document.querySelectorAll('input[type=file][data-target]').forEach(function (input) {
  input.addEventListener('change', function () {
    var area = document.getElementById(input.dataset.target);
    var note = document.querySelector('.file-name[data-for="' + input.dataset.target + '"]');
    var file = input.files && input.files[0];
    if (!file) return;
    note.classList.remove('error');
    if (file.size > Number(input.dataset.max)) {
      note.textContent = 'Use a file up to 64 KB.'; note.classList.add('error'); input.value = ''; return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      area.value = String(reader.result); area.classList.add('loaded'); input.value = '';
      note.textContent = file.name + ' loaded (' + Math.max(1, Math.round(file.size / 1024)) + ' KB)';
    };
    reader.onerror = function () { note.textContent = 'File read failed.'; note.classList.add('error'); };
    reader.readAsText(file);
  });
});
"""


def asset(name: str) -> Response:
    if name == "claim.css":
        return Response(CSS, media_type="text/css; charset=utf-8", headers={"Cache-Control": "public, max-age=3600"})
    if name == "claim.js":
        return Response(JS, media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "public, max-age=3600"})
    if name == "swb.svg":
        return Response((MARKS / "swb.svg").read_bytes(), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})
    if (p := PROVIDERS.get(name.removesuffix(".svg"))) and (MARKS / f"{p.mark}.svg").exists():
        return Response((MARKS / f"{p.mark}.svg").read_bytes(), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})
    return Response(status_code=404)
