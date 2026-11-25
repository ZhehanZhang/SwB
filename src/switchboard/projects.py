from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .audit import iso
from .config import SLUG_RE
from .store import Store

LINK_KEYS = ("capy_project", "repos", "claude_project", "chatgpt_project")


class ProjectError(ValueError):
    pass


@dataclass
class Project:
    slug: str
    name: str
    description: str = ""
    links: dict[str, Any] = field(default_factory=dict)
    archived: bool = False
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def scope(self) -> str:
        return f"project:{self.slug}"

    def as_dict(self) -> dict[str, Any]:
        return {"slug": self.slug, "scope": self.scope, "name": self.name, "description": self.description, "links": self.links,
                "archived": self.archived, "created_at": self.created_at, "updated_at": self.updated_at}


def _project(r: sqlite3.Row) -> Project:
    return Project(slug=r["slug"], name=r["name"], description=r["description"], links=json.loads(r["links"]),
                   archived=r["archived_at"] is not None, created_at=iso(r["created_at"]), updated_at=iso(r["updated_at"]))


def clean_links(links: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (links or {}).items():
        if k not in LINK_KEYS:
            raise ProjectError(f"unknown link {k!r}; use {', '.join(LINK_KEYS)}")
        if v in (None, "", []):
            continue
        if k == "repos":
            if isinstance(v, str):
                v = [v]
            out[k] = sorted({str(r).strip().lower() for r in v if str(r).strip()})
        else:
            out[k] = str(v).strip()
    return out


class Projects:
    """First-class projects: every memory belongs to one of them or to general."""

    def __init__(self, store: Store):
        self.store = store

    def get(self, slug: str, *, allow_archived: bool = True) -> Project:
        r = self.store.one("SELECT * FROM projects WHERE slug = ?", ((slug or "").strip().lower(),))
        if r is None:
            raise ProjectError(f"no project {slug!r}; see project_list, or create it with project_create")
        p = _project(r)
        if p.archived and not allow_archived:
            raise ProjectError(f"project {p.slug!r} is archived")
        return p

    def list(self, include_archived: bool = False) -> list[Project]:
        sql = "SELECT * FROM projects" + ("" if include_archived else " WHERE archived_at IS NULL") + " ORDER BY slug"
        return [_project(r) for r in self.store.q(sql)]

    def create(self, *, slug: str, name: str, description: str = "", links: dict[str, Any] | None = None) -> Project:
        slug = (slug or "").strip().lower()
        if not SLUG_RE.match(slug) or slug == "general":
            raise ProjectError("slug must be lowercase letters, digits and dashes (max 48), and not 'general'")
        if not (name or "").strip():
            raise ProjectError("name is required")
        if self.store.one("SELECT 1 FROM projects WHERE slug = ?", (slug,)):
            raise ProjectError(f"project {slug!r} already exists")
        now = self.store.clock()
        self.store.x(
            "INSERT INTO projects(slug, name, description, links, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (slug, name.strip(), description or "", json.dumps(clean_links(links)), now, now),
        )
        return self.get(slug)

    def update(self, slug: str, *, name: str | None = None, description: str | None = None, links: dict[str, Any] | None = None,
               new_slug: str | None = None, archived: bool | None = None) -> Project:
        p = self.get(slug)
        merged = dict(p.links)
        if links is not None:
            for k, v in links.items():
                merged.pop(k, None)
            merged.update(clean_links(links))
        target = (new_slug or p.slug).strip().lower()
        if target != p.slug and (not SLUG_RE.match(target) or target == "general"):
            raise ProjectError("new_slug must be lowercase letters, digits and dashes (max 48), and not 'general'")
        now = self.store.clock()
        current = self._archived_at(p.slug)
        archived_at = current if archived is None else ((current or now) if archived else None)

        def run(db: sqlite3.Connection) -> None:
            if target != p.slug:
                if db.execute("SELECT 1 FROM projects WHERE slug = ?", (target,)).fetchone():
                    raise ProjectError(f"project {target!r} already exists")
                db.execute("UPDATE memories SET scope = ? WHERE scope = ?", (f"project:{target}", p.scope))
                db.execute("UPDATE routes SET project = ? WHERE project = ?", (target, p.slug))
                db.execute("UPDATE handles SET project = ? WHERE project = ?", (target, p.slug))
            db.execute(
                "UPDATE projects SET slug = ?, name = ?, description = ?, links = ?, updated_at = ?, archived_at = ? WHERE slug = ?",
                (target, (name or p.name).strip(), p.description if description is None else description, json.dumps(merged), now, archived_at, p.slug),
            )

        self.store.tx(run)
        return self.get(target)

    def _archived_at(self, slug: str) -> float | None:
        r = self.store.one("SELECT archived_at FROM projects WHERE slug = ?", (slug,))
        return r["archived_at"] if r else None

    def resolve(self, hint: str) -> list[dict[str, Any]]:
        """Find projects matching a slug, name, Claude/ChatGPT project name, Capy project, or owner/repo."""
        h = (hint or "").strip().lower()
        if not h:
            raise ProjectError("pass a project hint: a name, a Claude or ChatGPT project name, a Capy project, or a repo")
        hits = []
        for p in self.list(include_archived=True):
            fields = {"slug": p.slug, "name": p.name.lower(), **{k: str(v).lower() for k, v in p.links.items() if k != "repos"}}
            exact = [k for k, v in fields.items() if v == h] + (["repos"] if h in p.links.get("repos", []) else [])
            partial = [k for k, v in fields.items() if h in v and k not in exact]
            if exact or partial:
                hits.append((0 if exact else 1, p, exact or partial))
        hits.sort(key=lambda t: (t[0], t[1].archived, t[1].slug))
        return [{**p.as_dict(), "matched": m, "exact": rank == 0} for rank, p, m in hits[:10]]
