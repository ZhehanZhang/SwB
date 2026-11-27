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
from .claims import FILE_KINDS, MAX_FILE_BYTES, Field, GroupFieldError
from .slack.avatar_files import BUNDLED_AVATARS, agent_avatar

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


def mark(provider: str) -> str:
    p = PROVIDERS.get(provider)
    if p is None:
        return ""
    if avatar := agent_avatar(p.key):
        body = f'<img class="persona-mark" src="/avatars/{avatar}" alt="" width="22" height="22">'
    elif (path := MARKS / f"{p.mark}.svg").is_file():
        body = path.read_text().strip().replace("<svg ", f'<svg class="mark mark-{p.key}" aria-hidden="true" ', 1)
    else:
        return ""
    return f'<span class="requester-tile" data-connector="{p.key}" title="{html.escape(p.title)}">{body}</span>'


def claim_field(f: Field, *, error: GroupFieldError | None = None) -> str:
    fid = f"f-{f.name}"
    get = (f'<a class="get" href="{html.escape(f.link)}" target="_blank" rel="noreferrer">Provider settings'
           '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M13 5h6v6m0-6-9 9m6-1v6H5V8h6"/></svg></a>') if f.link else ""
    label = html.escape(f.label) + (' <span class="optional">optional</span>' if f.optional else "")
    required = "" if f.optional else " required"
    note = ""
    if error is not None:
        required += ' aria-invalid="true"'
        if error.guidance:
            required += f' aria-describedby="{fid}-error"'
            note = f'<p id="{fid}-error" role="alert">{html.escape(error.guidance)}</p>'
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
    return f'<div class="field"><div class="field-head"><label for="{fid}">{label}</label>{get}</div>{control}{note}</div>'


def page(group: dict, csrf: str, *, owner_key: bool = False, host: str = "", now: float | None = None,
         error: GroupFieldError | None = None) -> HTMLResponse:
    fields = group["fields"]
    key = ('<div class="field"><label for="owner-key">SwB owner key</label><input id="owner-key" name="owner_key" type="password" required '
           'autocomplete="new-password" autocapitalize="none" spellcheck="false"></div>') if owner_key else ""
    expiry = f"Expires in {max(1, math.ceil((group['expires_at'] - now) / 60))} min" if now is not None else ""
    footer = f'<footer><span>{html.escape(host)}</span><span>{expiry}</span></footer>' if host or expiry else ""
    title = str(error) if error else "Save credentials"
    body = f"""{mark(group["provider"])}<h1>{html.escape(title)}</h1>
<p class="scope">For {html.escape(group["title"])}.</p>
<form method="post" autocomplete="off">
<input type="hidden" name="csrf" value="{html.escape(csrf)}">
{key}{"".join(claim_field(f, error=error if error and error.field == f else None) for f in fields)}
<div class="actions"><button type="submit"><svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>Save</button></div>
</form>{footer}"""
    return pages._claim_document(title, body, 400 if error else 200, field_assets=True)


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
    if p := PROVIDERS.get(name.removesuffix(".svg")):
        if avatar := agent_avatar(p.key):
            return Response((BUNDLED_AVATARS / avatar).read_bytes(), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
        if (path := MARKS / f"{p.mark}.svg").is_file():
            return Response(path.read_bytes(), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})
    return Response(status_code=404)
