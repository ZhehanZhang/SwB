"""Plain-text subjects for wakes and mailbox previews."""

from __future__ import annotations

import re
from html import unescape

from .trust import FramedText, neutralize


def _plain(text: str) -> str:
    text = neutralize(unescape(text))
    text = re.sub(r"(?m)^[ \t]*(?:>+[ \t]*|#{1,6}[ \t]+|[-*+][ \t]+|\d+[.)][ \t]+)+", "", text)
    text = re.sub(r"(?m)^[ \t]*(?:```|~~~)[\w+-]*[ \t]*$", "", text)
    text = re.sub(r"!?\[([^\[\]]+)\]\([^\s()]*\)", r"\1", text)
    text = re.sub(r"<([@#!][^\s<>|]+|(?:https?://|mailto:)[^\s<>|]+)(?:\|([^<>]+))?>",
                  lambda m: m[2] or (" " if m[1][0] in "@#!" else m[1]), text)
    for marker, width in ((r"\*", 3), ("_", 3), ("~", 2)):
        text = re.sub(rf"(?<!\w)({marker}{{1,{width}}})(?=\S)([^{marker}\n]+?)(?<=\S)\1(?!\w)", r"\2", text)
    text = "".join(char for char in text if char != "`" and (char.isprintable() or char.isspace()))
    return " ".join(word for word in neutralize(text).split() if word != "\u00b7")


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    end = limit - 3
    head = text[:end]
    if text[end] != " ":
        head = head.rpartition(" ")[0] or head
    return head.rstrip() + "..."


def subject(sender: str, platform: str, where: str | None, text: str, *, verb: str = "asks", trust: str = "owner") -> str:
    """Name the sender and the ask, without letting message formatting shape the subject."""
    sender, platform, place, text, verb = (_plain(value) for value in (sender, platform, where or "", text, verb))
    if not (sender or platform or place or verb):
        return _cut(text, 159)
    trust_label = f" ({_plain(trust)})" if trust != "owner" else ""
    location = " ".join(part for part in (platform, _cut(place, 45)) if part)
    prefix = " ".join(part for part in (_cut(sender, 44) + trust_label,
                                       ("on " if platform == "GitHub" else "in ") + location if location else "") if part)
    if not text:
        return _cut(prefix, 159)
    lead = " ".join(part for part in (prefix, verb) if part)
    lead = _cut(lead, 145) + ": " if lead else ""
    return lead + _cut(text, min(90, 159 - len(lead)))


def wake_text(subject: str, body: str, link: str | None, tail: str | None) -> str:
    """Put the subject first and the delivery instructions last, leaving the body intact."""
    text = "\n\n".join(part for part in (subject, body, link, tail) if part)
    return FramedText(text) if isinstance(body, FramedText) else text


def lead_with_subject(title: str, body: str) -> str:
    return body if body.partition("\n")[0] == title else wake_text(title, body, None, None)
