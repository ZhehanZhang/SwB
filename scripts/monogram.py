"""Make a square monogram PNG for a Slack persona: python scripts/monogram.py "Build Bot" build-bot.png [--bg "#5b6b7a"].

Drop the file into <data>/avatars or an avatar_dirs folder, named after the persona (or its avatar: entry), and new
bot posts use it.
"""

from __future__ import annotations

import argparse

from switchboard.slack.monogram import initials, render


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name", help="persona display name; its initials go on the picture")
    ap.add_argument("out", help="output .png path")
    ap.add_argument("--text", help="letters to draw instead of the initials")
    ap.add_argument("--bg", default="#5b6b7a")
    ap.add_argument("--fg", default="#ffffff")
    a = ap.parse_args()
    render(a.text or initials(a.name), a.bg, a.fg).save(a.out, optimize=True)


if __name__ == "__main__":
    main()
