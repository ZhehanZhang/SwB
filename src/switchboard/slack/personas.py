from __future__ import annotations

from typing import Any

from .config import Grant, Config, Persona, WorkspaceConfig

USER_POST_NOTE = "Personas apply only to bot posts; posts with the user identity always appear as the account owner."


class PersonaError(ValueError):
    pass


def resolve_persona(cfg: Config, ws: WorkspaceConfig, identity: str, key: Grant, requested: str | None) -> Persona | None:
    """Call argument (if the key may override) > key binding > workspace default."""
    if identity != "bot":
        if requested:
            raise PersonaError(USER_POST_NOTE)
        return None
    if requested:
        if not key.allow_persona_override:
            raise PersonaError(f"agent {key.id!r} may not choose a persona; it posts as its bound persona")
        if key.allowed_personas is not None and requested not in key.allowed_personas:
            raise PersonaError(f"agent {key.id!r} may not use persona {requested!r}")
        persona = cfg.personas.get(requested)
        if persona is None:
            raise PersonaError(f"unknown persona {requested!r}")
        return persona
    for pid in (key.persona, ws.default_persona):
        if pid and pid in cfg.personas:
            return cfg.personas[pid]
    return None


def persona_post_args(persona: dict[str, Any] | None) -> dict[str, Any]:
    if not persona:
        return {}
    args: dict[str, Any] = {"username": persona["name"]}
    if persona.get("icon_url"):
        args["icon_url"] = persona["icon_url"]
    elif persona.get("emoji"):
        args["icon_emoji"] = persona["emoji"]
    return args
