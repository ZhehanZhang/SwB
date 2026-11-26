"""Visibility: one private | shared switch on every switchboard resource, with one resolver.

A resource is <kind>:<id>: target:<slug> (agents, Captains, threads, routines, dots), channel:<workspace>/<id or #name>,
memory:<general | project:slug>, route:<id>, tool:<name>. Private (the default for every kind) means only the owner
reaches it: his verified Slack ids and his own signed-in agents. Shared means anyone in that workspace; a list narrows
it to Slack user ids and workspace:<label> entries. Tools are always private: a side-effect tool can't be shared.

Config sets the defaults per kind and any exceptions; the owner flips a resource from the root page or the
visibility_set tool, and that override is stored here and wins over the config. Every flip is audited by the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

KINDS = ("target", "channel", "memory", "route", "tool")
ALWAYS_PRIVATE = ("tool",)
RESOURCE_RE = re.compile(r"(target|channel|memory|route|tool):\S+")
ENTRY_RE = re.compile(r"[UWB][A-Z0-9]{2,}|workspace:[a-z0-9][a-z0-9_-]*")

SCHEMA = """
CREATE TABLE IF NOT EXISTS visibility (resource TEXT PRIMARY KEY, value TEXT NOT NULL, set_at REAL NOT NULL, set_by TEXT NOT NULL);
"""


class VisibilityError(ValueError):
    pass


@dataclass(frozen=True)
class Who:
    """Who is asking: owner is set only from verified data (his Slack ids, his own sign-ins), never from text."""

    owner: bool
    id: str | None = None
    workspace: str | None = None


def parse(value: Any, where: str = "visibility") -> str | tuple[str, ...]:
    if value in ("private", "shared"):
        return value
    if value is True:
        return "shared"
    if value is False or value is None:
        return "private"
    if isinstance(value, (list, tuple)) and value and all(isinstance(v, str) and ENTRY_RE.fullmatch(v) for v in value):
        return tuple(value)
    raise VisibilityError(f"{where} must be private, shared, or a list of Slack user ids and workspace:<label>")


def show(rule: str | tuple[str, ...]) -> str | list[str]:
    return list(rule) if isinstance(rule, tuple) else rule


class Visibility:
    def __init__(self, raw: dict[str, Any] | None = None, store: Any = None):
        raw = raw or {}
        self.store = store
        self.defaults: dict[str, str | tuple[str, ...]] = {k: "private" for k in KINDS}
        for kind, value in (raw.get("defaults") or {}).items():
            if kind not in KINDS:
                raise VisibilityError(f"visibility.defaults.{kind}: unknown kind; kinds are {', '.join(KINDS)}")
            self.defaults[kind] = self._check(kind, parse(value, f"visibility.defaults.{kind}"))
        self.config: dict[str, str | tuple[str, ...]] = {}
        for res, value in (raw.get("resources") or {}).items():
            key = self.key(str(res))
            self.config[key] = self._check(key.partition(":")[0], parse(value, f"visibility.resources.{res}"))
        if store is not None:
            store.x(SCHEMA)

    @staticmethod
    def key(resource: str) -> str:
        res = resource.strip().lower()
        if not RESOURCE_RE.fullmatch(res):
            raise VisibilityError(f"{resource!r} is not a resource; use <kind>:<id> with kind one of {', '.join(KINDS)}")
        return res

    @staticmethod
    def _check(kind: str, rule: str | tuple[str, ...]) -> str | tuple[str, ...]:
        if kind in ALWAYS_PRIVATE and rule != "private":
            raise VisibilityError(f"{kind} resources are always private; side-effect tools can't be shared")
        return rule

    def _override(self, key: str) -> str | tuple[str, ...] | None:
        if self.store is None:
            return None
        row = self.store.q("SELECT value FROM visibility WHERE resource = ?", (key,))
        if not row:
            return None
        value = row[0]["value"]
        return tuple(value.split(",")) if "," in value or value not in ("private", "shared") else value

    def explicit(self, resource: str) -> str | tuple[str, ...] | None:
        key = self.key(resource)
        found = self._override(key)
        return found if found is not None else self.config.get(key)

    def rule(self, *resources: str) -> str | tuple[str, ...]:
        """The first explicit rule among resources (aliases of one thing, e.g. a channel's id and #name), else the kind's default."""
        for res in resources:
            found = self.explicit(res)
            if found is not None:
                return found
        return self.defaults[self.key(resources[0]).partition(":")[0]]

    def allows(self, who: Who, *resources: str) -> bool:
        if who.owner:
            return True
        rule = self.rule(*resources)
        if rule == "private":
            return False
        if rule == "shared":
            return True
        return bool((who.id and who.id in rule) or (who.workspace and f"workspace:{who.workspace}" in rule))

    def set(self, resource: str, value: Any, actor: str, now: float) -> str | list[str]:
        key = self.key(resource)
        rule = self._check(key.partition(":")[0], parse(value, resource))
        if self.store is None:
            raise VisibilityError("visibility overrides need switchboard's store")
        stored = ",".join(rule) if isinstance(rule, tuple) else rule
        self.store.x("INSERT INTO visibility(resource, value, set_at, set_by) VALUES (?,?,?,?) ON CONFLICT(resource) DO UPDATE SET "
                     "value = excluded.value, set_at = excluded.set_at, set_by = excluded.set_by", (key, stored, now, actor))
        return show(rule)

    def rows(self, extra: list[str] = ()) -> list[dict[str, Any]]:
        """Every resource with a rule (config or override) plus extra ones (known targets), for the page and the tool."""
        keys = dict.fromkeys([*self.config, *(self.key(e) for e in extra)])
        overrides = {r["resource"]: r for r in self.store.q("SELECT * FROM visibility")} if self.store is not None else {}
        keys.update(dict.fromkeys(overrides))
        out = []
        for key in keys:
            rule = self.rule(key)
            src = "override" if key in overrides else "config" if key in self.config else "default"
            out.append({"resource": key, "visibility": show(rule), "source": src, "fixed": key.partition(":")[0] in ALWAYS_PRIVATE})
        return sorted(out, key=lambda r: r["resource"])
