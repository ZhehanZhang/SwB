from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .config import AVATAR_RE

BUNDLED_AVATARS = Path(__file__).parent / "avatars"
AGENT_AVATARS = {
    "claude-code": "claude-code.png", "claude": "claude.png", "anthropic": "claude.png",
    "chatgpt": "chatgpt.png", "openai": "chatgpt.png", "dots": "chatgpt.png", "dot": "chatgpt.png",
    "codex": "codex.png", "gemini": "gemini.png", "grok": "grok.png", "xai": "grok.png",
    "muse": "muse.png", "swb": "swb.png", "switchboard": "swb.png",
}


def agent_avatar(name: str) -> str | None:
    name = name.lower().replace("_", "-").replace(" ", "-").partition(":")[0]
    return next((filename for prefix, filename in AGENT_AVATARS.items() if name == prefix or name.startswith(prefix + "-")), None)


def agent_avatar_url(name: str) -> str | None:
    return f"/avatars/{avatar}" if (avatar := agent_avatar(name)) else None


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
