"""Captain avatars for SwB personas, drawn the way the Capy web app draws them.

On by default (set blocks.capy.avatars or slack.captain_avatars to false to use monograms): Capy's API returns each Captain's avatar as
{kind: preset|emoji, value, background, image?}. As of 2026-11 the web app (capy.ai/assets/index-*.js) shows a preset
as <img src="/avatars/<preset>.png"> filling a rounded square whose CSS background is avatar.background, an emoji as
the character on that background, and image as that URL. /avatars/<preset>.png is a public, unhashed 512 px PNG with
a transparent background; it is not part of Capy's documented API and may move. Every failure falls back to a
monogram on the Captain's background colour, which is also what runs with the option off. Emoji avatars become a
monogram too (no colour emoji font in the image). Without an avatar spec, use the bundled Capy mark.

Files are cached per Captain and by what they were drawn from. A changed avatar gets a new file name and URL.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import httpx

from ...slack.avatar_files import BUNDLED_AVATARS
from ...targets import slugify

log = logging.getLogger("switchboard.slack.avatars")
ASSET_BASE = "https://capy.ai/avatars/"
SIZE = 512


def cache_name(name: str, avatar: dict[str, Any] | None, capy_assets: bool, captain_id: str | None = None) -> str:
    key = json.dumps([captain_id, avatar or {}, capy_assets], sort_keys=True)
    return f"captain-{slugify(name) or 'x'}-{hashlib.sha256(key.encode()).hexdigest()[:10]}.png"


async def _fetch(http: httpx.AsyncClient, url: str) -> bytes:
    r = await http.get(url, timeout=15, follow_redirects=True)
    r.raise_for_status()
    if len(r.content) > 5 * 1024 * 1024:
        raise ValueError("avatar image too large")
    return r.content


def _cover(data: bytes) -> Any:
    from PIL import Image

    img = Image.open(io.BytesIO(data)).convert("RGBA")
    scale = SIZE / min(img.size)
    img = img.resize((max(SIZE, round(img.width * scale)), max(SIZE, round(img.height * scale))))
    left, top = (img.width - SIZE) // 2, (img.height - SIZE) // 2
    return img.crop((left, top, left + SIZE, top + SIZE))


async def render_captain(http: httpx.AsyncClient, out_dir: Path, name: str, avatar: dict[str, Any] | None, capy_assets: bool,
                         *, captain_id: str | None = None) -> str | None:
    """Write the Captain's avatar PNG into out_dir if it is not there yet and return its file name (None without Pillow)."""
    fname = cache_name(name, avatar, capy_assets, captain_id)
    path = out_dir / fname
    if path.is_file():
        return fname
    if not avatar:
        data = (BUNDLED_AVATARS / "capy.png").read_bytes()
    else:
        data = await _render(http, name, avatar, capy_assets)
        if data is None:
            return None
    out_dir.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=out_dir) as tmp:
        tmp.write(data)
        tmp.flush()
        with suppress(FileExistsError):
            path.hardlink_to(tmp.name)
    return fname


async def _render(http: httpx.AsyncClient, name: str, avatar: dict[str, Any], capy_assets: bool) -> bytes | None:
    try:
        from PIL import Image

        from ...slack.monogram import color, initials, png, render
    except ImportError:
        return None
    bg = color(avatar.get("background"), "#9a6b45" if capy_assets else "#5b6b7a")
    img, square = None, False
    if capy_assets:
        try:
            if isinstance(avatar.get("image"), str) and avatar["image"].startswith("https://"):
                img, square = _cover(await _fetch(http, avatar["image"])), True
            elif avatar.get("kind") == "preset" and isinstance(avatar.get("value"), str) and avatar["value"].replace("-", "").isalnum():
                face = _cover(await _fetch(http, ASSET_BASE + avatar["value"] + ".png"))
                img = Image.new("RGBA", (SIZE, SIZE), bg)
                img.alpha_composite(face)
                square = True
        except Exception as exc:
            log.info("Captain avatar for %s fell back to a monogram: %s", name, type(exc).__name__)
            img, square = None, False
    if img is None:
        img = render(initials(name), bg)
    return png(img.convert("RGB") if square else img)
