"""Square monogram PNGs for personas without a picture (Pillow)."""

from __future__ import annotations

import io
import re

from PIL import Image, ImageDraw, ImageFont

NEUTRAL = "#5b6b7a"


def initials(name: str) -> str:
    parts = [p for p in re.split(r"[\s_-]+|(?<=[a-z])(?=[A-Z])", name or "") if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (name or "?")[:2].capitalize()


def color(value: str | None, default: str = NEUTRAL) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) else default


def render(text: str, bg: str = NEUTRAL, fg: str = "#ffffff", size: int = 512, radius: float = 0.22) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * radius), fill=color(bg))
    font = ImageFont.load_default(size=int(size * (0.42 if len(text) <= 2 else 0.3)))
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((size - (box[2] - box[0])) / 2 - box[0], (size - (box[3] - box[1])) / 2 - box[1]), text, font=font, fill=fg)
    return img


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
