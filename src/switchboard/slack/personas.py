from __future__ import annotations

from typing import Any, Callable, Mapping

from .config import Grant, Config, Persona, WorkspaceConfig

USER_POST_NOTE = "Personas apply only to bot posts; posts with the user identity always appear as the account owner."


class PersonaError(ValueError):
    pass


def resolve_persona(cfg: Config, ws: WorkspaceConfig, identity: str, key: Grant, requested: str | None,
                    personas: Mapping[str, Persona] | None = None, thread_persona: str | None = None) -> Persona | None:
    """Call argument (if the agent may override) > the agent's persona for this workspace > its bound persona >
    the persona named like the agent (claude, chatgpt, ...) > the workspace default. Mirrors the send-key chain.
    thread_persona comes first: the caller passes it only when the agent is the one behind the thread's target (a block
    agent posting in a thread routed to one of that block's targets, like Capy in a Captain's thread)."""
    personas = cfg.personas if personas is None else personas
    if identity != "bot":
        if requested:
            raise PersonaError(USER_POST_NOTE)
        return None
    if requested:
        if not key.allow_persona_override:
            raise PersonaError(f"agent {key.id!r} may not choose a persona; it posts as its bound persona")
        if key.allowed_personas is not None and requested not in key.allowed_personas:
            raise PersonaError(f"agent {key.id!r} may not use persona {requested!r}")
        persona = personas.get(requested)
        if persona is None:
            raise PersonaError(f"unknown persona {requested!r}")
        return persona
    chain = (thread_persona, key.persona_by_workspace.get(ws.label), key.persona, key.id, ws.default_persona)
    for pid in chain:
        if pid and pid in personas:
            return personas[pid]
    return None


def persona_post_args(persona: dict[str, Any] | None, avatar_url: Callable[[str], str | None] | None = None) -> dict[str, Any]:
    if not persona:
        return {}
    args: dict[str, Any] = {"username": persona["name"]}
    url = avatar_url(persona["avatar"]) if avatar_url and persona.get("avatar") else None
    if url:
        args["icon_url"] = url
    elif persona.get("icon_url"):
        args["icon_url"] = persona["icon_url"]
    elif persona.get("emoji"):
        args["icon_emoji"] = persona["emoji"]
    return args
