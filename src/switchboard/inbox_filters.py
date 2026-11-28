"""Owner console inbox queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

_FILTERS = ("sender", "recipient", "channel", "block", "kind", "status", "q")
_SENDER = """COALESCE(json_extract(h.meta, '$.display_from'), json_extract(h.meta, '$."from"'), json_extract(h.meta, '$.source'), (
    SELECT json_extract(i.data, '$."from"') FROM inbox i
    WHERE i.handle = h.handle AND json_type(i.data, '$."from"') = 'object' ORDER BY i.id LIMIT 1
))"""
_SENDER_LABELS = tuple(f"json_extract({_SENDER}, '$.{key}')" for key in
                       ("name", "label", "agent", "user_name", "author", "user", "kind"))
_TARGET = """COALESCE(h.target, (SELECT json_extract(i.data, '$.target') FROM inbox i
            WHERE i.handle = h.handle AND json_type(i.data, '$.target') = 'text' ORDER BY i.id LIMIT 1))"""
_TARGET_LABELS = (_TARGET, "json_extract(h.meta, '$.target.name')",
                  "json_extract(h.meta, '$.dot')", "json_extract(h.meta, '$.channel_name')")
_CHANNEL_LABELS = (
    "h.ref", _TARGET, "json_extract(h.meta, '$.channel_mode.channel')", "json_extract(h.meta, '$.channel_mode.space')",
    *(f"json_extract({_SENDER}, '$.{key}')" for key in ("channel", "channel_name", "workspace", "space")),
)
_KIND = "(SELECT i.kind FROM inbox i WHERE i.handle = h.handle ORDER BY i.id LIMIT 1)"
_MODE_PROJECT = """COALESCE(json_type(h.meta, '$.channel_mode.project'), (
    SELECT json_type(i.data, '$.channel_mode.project') FROM inbox i WHERE i.handle = h.handle ORDER BY i.id LIMIT 1
))"""
_PROJECT = """CASE WHEN """ + _MODE_PROJECT + """ = 'null' THEN NULL ELSE
    COALESCE(json_extract(h.meta, '$.channel_mode.project'), (
        SELECT json_extract(i.data, '$.channel_mode.project') FROM inbox i WHERE i.handle = h.handle ORDER BY i.id LIMIT 1
    ), h.project) END"""
_UNREAD = """EXISTS (SELECT 1 FROM inbox i WHERE i.handle = h.handle AND i.id > COALESCE((
                SELECT MAX(cursor) FROM inbox_cursors c WHERE c.owner = 'owner'
                AND (c.handle = h.handle OR (i.owner = 'owner' AND c.handle = '*'))
              ), 0))"""


def _contains(columns: tuple[str, ...], terms: tuple[str, ...]) -> tuple[str, list[str]]:
    sql = " OR ".join(f"instr(lower(COALESCE({col}, '')), lower(?)) > 0" for col in columns for _ in terms)
    return f"({sql})", list(terms) * len(columns)


@dataclass(frozen=True)
class InboxFilters:
    values: dict[str, str]
    older_first: bool

    @classmethod
    def parse(cls, params: Mapping[str, str]) -> InboxFilters:
        values = {key: value for key in (*_FILTERS, "unread") if (value := params.get(key, "").strip())}
        for key, value in values.items():
            if len(value) > (500 if key == "q" else 200) or "\x00" in value:
                raise ValueError(f"invalid {key}")
        if values.get("unread") not in (None, "1", "0"):
            raise ValueError("invalid unread")
        sort = params.get("sort", "newest")
        if sort not in ("newest", "oldest"):
            raise ValueError("invalid sort")
        return cls(values, sort == "oldest")

    def clauses(self, core: Any, project: str | None) -> list[tuple[str, list[str]]]:
        clauses = []
        aliases = {label.casefold(): ident for ident, spec in core.agents.specs.items() if (label := spec.label)}
        if project:
            fallback, args = _route_project_clause(core, project)
            clauses.append((f"({_PROJECT} = ? OR ({_MODE_PROJECT} IS NULL AND h.project IS NULL AND {fallback}))", [project, *args]))
        for key, value in self.values.items():
            terms = tuple(dict.fromkeys((value, aliases.get(value.casefold(), value))))
            if key == "sender":
                predicate, arguments = _contains((*_SENDER_LABELS, "h.owner"), terms)
                if value.casefold() in ("github watch", "watch"):
                    blocks = ["github", *(name for name, block in core.blocks.items() if block.manifest.id == "github")]
                    marks = ", ".join("?" for _ in blocks)
                    predicate = f"""({predicate} OR ({_KIND} = 'watch_digest' AND EXISTS (
                        SELECT 1 FROM inbox i WHERE i.handle = h.handle AND json_extract(i.data, '$.block') IN ({marks})
                    )))"""
                    arguments += blocks
                clauses.append((predicate, arguments))
            elif key == "recipient":
                clauses.append(_contains(_TARGET_LABELS, terms))
            elif key == "channel":
                clauses.append(_contains(_CHANNEL_LABELS, (value.removeprefix("#") or value,)))
            elif key == "block":
                blocks = [name for name, block in core.blocks.items() if block.manifest.id == value.lower()]
                choices = list(dict.fromkeys((value, *blocks)))
                marks = ", ".join("?" for _ in choices)
                clauses.append((f"""(h.destination IN ({marks}) OR json_extract(h.meta, '$.channel_mode.block') IN ({marks})
                                     OR json_extract({_SENDER}, '$.block') IN ({marks})
                                     OR json_extract({_SENDER}, '$.kind') IN ({marks})
                                     OR EXISTS (SELECT 1 FROM inbox i WHERE i.handle = h.handle
                                     AND json_extract(i.data, '$.block') IN ({marks})))""", choices * 5))
            elif key == "kind":
                clauses.append((f"COALESCE({_KIND}, 'conversation') = ?", [value]))
            elif key == "status":
                clauses.append(("h.status = ?", [value]))
            elif key == "unread":
                clauses.append((_UNREAD if value == "1" else f"NOT {_UNREAD}", []))
            elif key == "q":
                summary, args = _contains(("h.title", *_SENDER_LABELS, *_TARGET_LABELS, "h.owner"), terms)
                content, content_args = _contains(("json_extract(i.data, '$.text')", "json_extract(i.data, '$.subject')",
                                                    "json_extract(i.data, '$.items')"), (value,))
                clauses.append((f"({summary} OR EXISTS (SELECT 1 FROM inbox i WHERE i.handle = h.handle AND {content}))",
                                args + content_args))
        return clauses


def _route_project_clause(core: Any, project: str) -> tuple[str, list[str]]:
    parts, args = [], []
    for name, block in core.blocks.items():
        if block.manifest.id not in ("telegram", "discord"):
            continue
        for label, space in block.spaces.items():
            if space.project == project:
                parts.append("(h.destination = ? AND (h.ref = ? OR substr(h.ref, 1, ?) = ?))")
                args.extend((name, label, len(label) + 1, label + ":"))
    return f"({' OR '.join(parts)})" if parts else "0", args

def unread_handles(core: Any, handles: list[str]) -> set[str]:
    if not handles:
        return set()
    marks = ", ".join("?" for _ in handles)
    return {row["handle"] for row in core.store.q(
        f"SELECT h.handle FROM handles h WHERE h.handle IN ({marks}) AND {_UNREAD}", handles,
    )}
