from __future__ import annotations

import asyncio
import base64
import binascii
import re
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import Image, ImageOps

from .store import Store

DEFAULT_NAME = "SwB"
DEFAULT_ICON = "swb.png"
MAX_ICON_BYTES = 2 * 1024 * 1024
MAX_IDENTITY_BODY = ((MAX_ICON_BYTES + 2) // 3) * 4 + 4096
BOT_ICON = re.compile(r"bot-[0-9a-f]{48}\.png")


def normalize_icon(raw: str) -> bytes:
    if len(raw) > MAX_IDENTITY_BODY - 4096:
        raise ValueError("icon limits")
    try:
        data = base64.b64decode(raw, validate=True)
        if len(data) > MAX_ICON_BYTES:
            raise ValueError("icon limits")
        with Image.open(BytesIO(data), formats=["PNG", "JPEG", "WEBP"]) as image:
            if max(image.size) > 2048 or getattr(image, "n_frames", 1) != 1:
                raise ValueError("icon limits")
            icon = ImageOps.fit(ImageOps.exif_transpose(image).convert("RGBA"), (512, 512), Image.Resampling.LANCZOS)
            output = BytesIO()
            icon.save(output, "PNG")
            return output.getvalue()
    except (binascii.Error, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("invalid icon") from exc


@dataclass(frozen=True)
class BotIdentity:
    name: str
    icon: str


class BotIdentities:
    def __init__(self, store: Store, data_dir: Path):
        self.store = store
        self.directory = data_dir / "bot-identities"
        self.write_lock = asyncio.Lock()
        store.x("CREATE TABLE IF NOT EXISTS bot_identities ("
                "block TEXT NOT NULL, space TEXT NOT NULL, name TEXT NOT NULL, icon TEXT NOT NULL, PRIMARY KEY (block, space))")

    def get(self, block: str, space: str = "") -> BotIdentity | None:
        row = self.store.one("SELECT name, icon FROM bot_identities WHERE block = ? AND space = ?", (block, space))
        return BotIdentity(row["name"], row["icon"]) if row else None

    def set(self, block: str, space: str, identity: BotIdentity | None) -> None:
        if identity is None:
            self.store.x("DELETE FROM bot_identities WHERE block = ? AND space = ?", (block, space))
        else:
            self.store.x("INSERT OR REPLACE INTO bot_identities (block, space, name, icon) VALUES (?, ?, ?, ?)",
                         (block, space, identity.name, identity.icon))

    def save_icon(self, data: bytes) -> str:
        name = f"bot-{sha256(data).hexdigest()[:48]}.png"
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / name
        if not path.is_file():
            with NamedTemporaryFile(dir=self.directory, delete=False) as temp:
                temp.write(data)
            staged = Path(temp.name)
            try:
                staged.replace(path)
            finally:
                staged.unlink(missing_ok=True)
        return name

    def icon_path(self, filename: str) -> Path | None:
        path = self.directory / filename if BOT_ICON.fullmatch(filename) else None
        return path if path is not None and path.is_file() else None

    def icon_bytes(self, filename: str) -> bytes | None:
        path = self.icon_path(filename)
        return path.read_bytes() if path else None
