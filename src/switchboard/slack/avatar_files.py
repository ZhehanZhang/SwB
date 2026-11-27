from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .config import AVATAR_RE

BUNDLED_AVATARS = Path(__file__).parent / "avatars"


def find_avatar(name: str, dirs: Iterable[Path] = (BUNDLED_AVATARS,)) -> Path | None:
    if not AVATAR_RE.fullmatch(name):
        return None
    return next((path for directory in dirs if (path := directory / name).is_file()), None)


class AvatarFiles:
    def __init__(self, data_dir: Path, public_base_url: str | None, dirs: Iterable[str] = ()):
        self.uploads = data_dir / "avatars"
        self.rendered = data_dir / "avatar-cache"
        self.dirs = (self.uploads, *(Path(d) for d in dirs), self.rendered, BUNDLED_AVATARS)
        self.public_base_url = public_base_url

    def find_avatar(self, name: str) -> Path | None:
        return find_avatar(name, self.dirs)

    def avatar_url(self, name: str) -> str | None:
        """Public avatar URL, versioned so clients refetch a replaced picture."""
        if not self.public_base_url:
            return None
        path = self.find_avatar(name)
        if path is None:
            return None
        try:
            version = path.stat().st_mtime_ns
        except FileNotFoundError:
            return None
        return f"{self.public_base_url}/avatars/{name}?v={version}"
