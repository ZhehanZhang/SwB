import hashlib

from switchboard.slack.avatar_files import BUNDLED_AVATARS


def test_bundled_avatars_match_manifest_and_public_routes(make):
    _, client = make()
    checksums = dict(
        (name, digest) for digest, name in (
            line.split() for line in (BUNDLED_AVATARS / "SHA256SUMS").read_text().splitlines()
        )
    )
    assert set(checksums) == {
        "chatgpt.png", "claude-code.png", "claude.png", "codex.png",
        "gemini.png", "grok.png", "muse.png", "swb.png",
    }
    for name, digest in checksums.items():
        data = (BUNDLED_AVATARS / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, name
        avatar = client.get(f"/avatars/{name}")
        assert avatar.status_code == 200 and avatar.headers["content-type"] == "image/png"
        assert avatar.content == data
    assert client.get("/avatars/switchboard.png").content == (BUNDLED_AVATARS / "swb.png").read_bytes()
