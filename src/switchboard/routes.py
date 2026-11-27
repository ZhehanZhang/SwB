"""Routes: which destination target an origin identity (an agent, a project, or both) is pointed at."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .audit import iso
from .store import Store


class RouteError(ValueError):
    pass


def route_audit_name(value: Any) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 120 and value.isprintable() and "://" not in value else None


@dataclass
class Route:
    id: int
    destination: str
    agent: str | None
    project: str | None
    target: dict[str, Any]
    defaults: dict[str, Any] = field(default_factory=dict)
    allow_override: bool = False
    note: str = ""
    updated_at: str | None = None
    updated_by: str = ""

    @property
    def level(self) -> str:
        if self.agent and self.project:
            return "agent+project"
        if self.project:
            return "project"
        if self.agent:
            return "agent"
        return "global"

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "destination": self.destination, "agent": self.agent, "project": self.project, "level": self.level,
                "target": self.target, "defaults": self.defaults, "allow_override": self.allow_override, "note": self.note,
                "updated_at": self.updated_at, "updated_by": self.updated_by}

    def audit_excerpt(self) -> str:
        repos = self.defaults.get("repos")
        name = route_audit_name(self.target.get("name"))
        captain = self.target.get("captain")
        if isinstance(captain, str) and captain.startswith("jam_") and (not name or name == captain):
            name = route_audit_name(f"{self.destination}:captain:{captain}")
        return json.dumps({"target_name": name, "repo_count": len(repos) if isinstance(repos, list) else 0},
                          ensure_ascii=False)


def _route(r: Any) -> Route:
    return Route(id=r["id"], destination=r["destination"], agent=r["agent"] or None, project=r["project"] or None,
                 target=json.loads(r["target"]), defaults=json.loads(r["defaults"]), allow_override=bool(r["allow_override"]),
                 note=r["note"], updated_at=iso(r["updated_at"]), updated_by=r["updated_by"])


class Routes:
    """Resolution order: agent + project, then project, then agent, then the global route."""

    def __init__(self, store: Store):
        self.store = store

    def list(self, destination: str | None = None) -> list[Route]:
        sql = "SELECT * FROM routes" + (" WHERE destination = ?" if destination else "") + " ORDER BY destination, project, agent"
        return [_route(r) for r in self.store.q(sql, (destination,) if destination else ())]

    def set(self, *, destination: str, agent: str | None, project: str | None, target: dict[str, Any], defaults: dict[str, Any] | None,
            allow_override: bool, note: str, by: str) -> Route:
        agent = (agent or "").strip().lower()
        project = (project or "").strip().lower().removeprefix("project:")
        self.store.x(
            """INSERT INTO routes(destination, agent, project, target, defaults, allow_override, note, updated_at, updated_by) VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(destination, agent, project) DO UPDATE SET target = excluded.target, defaults = excluded.defaults,
               allow_override = excluded.allow_override, note = excluded.note, updated_at = excluded.updated_at, updated_by = excluded.updated_by""",
            (destination, agent, project, json.dumps(target), json.dumps(defaults or {}), int(allow_override), note or "", self.store.clock(), by),
        )
        return _route(self.store.one("SELECT * FROM routes WHERE destination = ? AND agent = ? AND project = ?", (destination, agent, project)))

    def _match(self, destination: str | tuple[str, ...], agent: str, project: str) -> Any:
        for name in (destination,) if isinstance(destination, str) else destination:
            row = self.store.one("SELECT * FROM routes WHERE destination = ? AND agent = ? AND project = ?", (name, agent, project))
            if row is not None:
                return row
        return None

    def remove(self, *, destination: str | tuple[str, ...], agent: str | None, project: str | None) -> Route:
        agent, project = (agent or "").strip().lower(), (project or "").strip().lower().removeprefix("project:")
        r = self._match(destination, agent, project)
        if r is None:
            raise RouteError("no such route")
        self.store.x("DELETE FROM routes WHERE id = ?", (r["id"],))
        return _route(r)

    def resolve(self, *, destination: str | tuple[str, ...], agent: str, project: str | None) -> tuple[Route | None, list[str]]:
        """The route for this caller, and the levels checked in order (for explaining the choice)."""
        agent, project = (agent or "").strip().lower(), (project or "").strip().lower().removeprefix("project:")
        candidates = []
        if project:
            candidates += [("agent+project", agent, project), ("project", "", project)]
        candidates += [("agent", agent, ""), ("global", "", "")]
        tried = []
        for level, a, p in candidates:
            tried.append(level)
            r = self._match(destination, a, p)
            if r is not None:
                return _route(r), tried
        return None, tried
