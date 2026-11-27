from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from ...chat import ChatBlock
from ...memory import GENERAL, MemoryError_
from ...refusals import (CONFIRMATION_REQUIRED, CONVERSATION_REQUIRED, GRANT_REQUIRED, MEMORY_WRITER_REQUIRED, MODE_REQUIRED,
                         OWNER_REQUIRED, PROJECT_LINK_REQUIRED, ROUTE_TARGET_KIND, SCOPE_REQUIRED, TARGET_REQUIRED)

if TYPE_CHECKING:
    from mcp_types import Tool

    from ...auth import Principal
    from ...core import Switchboard
    from ...projects import Project

Role = Literal["owner", "self", "grant", "owner_or_writer", "route", "setup", "companion"]
ReadRole = Literal["message", "conversation", "provider_ref", "memory", "memory_scope", "projects", "owner"]
ReadPolicy = tuple[ReadRole, str | None]

COMPANION_ROUTES: dict[str, Role] = {
    "/companion/hello": "companion",
    "/companion/events": "companion",
    "/companion/results": "companion",
    "/companion/claims": "companion",
    "/companion/emit": "companion",
    "/companion/revoke": "companion",
}

READ_ROLES: dict[str, ReadPolicy] = {
    "get_message": ("message", "message_id"),
    "result": ("provider_ref", "conversation"),
    "status": ("provider_ref", "conversation"),
    "wait": ("conversation", "conversation"),
    "inbox": ("conversation", "conversation"),
    "capy_result": ("provider_ref", "thread_id"),
    "capy_status": ("provider_ref", "thread_id"),
    "capy_wait": ("conversation", "handle"),
    "capy_inbox": ("conversation", "handle"),
    "memory_read": ("memory", "id"),
    "memory_search": ("memory_scope", None),
    "memory_list": ("memory_scope", None),
    "context_pack": ("memory_scope", None),
    "project_list": ("projects", None),
    "capy_captain_digest": ("owner", None),
    "visibility_list": ("owner", None),
    "connect_list": ("owner", None),
    "companion_list": ("owner", None),
}
REFERENCE_ARGS = frozenset({"id", "message_id", "conversation", "handle", "thread_id", "ref"})

MUTATION_ROLES: dict[str, Role] = {
    "space_mode": "owner",
    "project_create": "owner",
    "project_update": "owner",
    "project_sync": "owner",
    "routes_set": "route",
    "routes_remove": "route",
    "memory_write": "grant",
    "memory_delete": "owner_or_writer",
    "memory_move": "owner_or_writer",
    "memory_promote": "owner",
    "login_link": "owner",
    "visibility_set": "owner",
    "connect_code": "owner",
    "connect_revoke": "owner",
    "companion_code": "owner",
    "companion_invoke": "owner",
    "companion_revoke": "owner",
    "connect": "setup",
    "send": "grant",
    "agent_send": "grant",
    "reply": "self",
    "cancel": "self",
    "wait": "self",
    "inbox": "self",
    "capy_delegate": "grant",
    "capy_reply": "self",
    "capy_cancel": "self",
    "capy_wait": "self",
    "capy_inbox": "self",
    "routine_add": "owner",
    "routine_remove": "owner",
    "routine_register": "owner",
    "routine_pickup": "self",
    "slack_post": "grant",
    "slack_react": "grant",
    "slack_request_write": "grant",
    "slack_wait": "self",
}

CHAT_ROLES: dict[str, Role] = {"post": "grant", "provision": "owner"}
BLOCK_ROLES: dict[str, dict[str, Role]] = {
    "github": {
        "label": "grant", "comment": "grant", "open_issue": "grant",
        "link_duplicate": "grant", "notify_owner": "grant", "reply": "grant",
    },
    "webhook": {"create": "owner", "rotate": "owner", "revoke": "owner", "test": "owner"},
}
BLOCK_READ_ROLES: dict[str, dict[str, ReadPolicy]] = {"webhook": {"list": ("owner", None)}}

AUTH_REQUIRED = "Sign in to call tools."
ROUTE_REQUIRED = "Use a route for your own agent or sign in as the owner."
CONVERSATION_NOT_FOUND = "Conversation not found."
CONVERSATION_CONFLICT = "Pass the same ID for conversation and handle."
MESSAGE_NOT_FOUND = "message not found"
MEMORY_NOT_FOUND = "memory not found"
TOOL_REQUIRED = "Use a key granted this tool."
POLICY_REQUIRED = "Use a tool with a SwB write policy."

REFUSAL_DETAILS = frozenset({
    AUTH_REQUIRED, OWNER_REQUIRED, ROUTE_REQUIRED, CONVERSATION_REQUIRED, SCOPE_REQUIRED, TARGET_REQUIRED, TOOL_REQUIRED,
    POLICY_REQUIRED, MODE_REQUIRED, CONFIRMATION_REQUIRED, GRANT_REQUIRED, MEMORY_WRITER_REQUIRED,
    CONVERSATION_NOT_FOUND, CONVERSATION_CONFLICT, MESSAGE_NOT_FOUND, MEMORY_NOT_FOUND, PROJECT_LINK_REQUIRED, ROUTE_TARGET_KIND,
})


def catalog_refusal(p: Principal | None, name: str, tool: Tool | None, role: Role | None, read_role: ReadPolicy | None) -> str | None:
    if p is None or p.kind in ("inbox_key", "companion"):
        return AUTH_REQUIRED
    if p.tools is not None and name not in p.tools:
        return TOOL_REQUIRED
    if tool is not None:
        if tool.annotations and tool.annotations.read_only_hint:
            if read_role is None and REFERENCE_ARGS.intersection(tool.input_schema.get("properties", {})):
                return CONVERSATION_NOT_FOUND
        elif role is None:
            return POLICY_REQUIRED
    if not p.is_unrestricted_owner and (role == "owner" or (read_role and read_role[0] == "owner")):
        return OWNER_REQUIRED
    return None


def can_read_conversation(core: Switchboard, p: Principal, handle: dict[str, Any] | None) -> bool:
    if handle is None:
        return False
    scope = f'project:{handle["project"]}' if handle["project"] else GENERAL
    return p.is_unrestricted_owner or (p.allows_routing_scope(scope) and core.inbox.participant(handle, p.source))


def granted_memory_scopes(core: Switchboard, p: Principal, scopes: list[str]) -> list[str]:
    return [s for s in scopes if core.allows_memory(p, s.strip().lower())]


def default_memory_scopes(core: Switchboard, p: Principal) -> list[str]:
    return list(p.memory_grants) if p.kind == "connect" else granted_memory_scopes(core, p, [GENERAL])


def read_refusal(core: Switchboard, p: Principal, name: str, arguments: dict[str, Any], policy: ReadPolicy) -> str | None:
    role, parameter = policy
    reference = arguments.get(parameter) if parameter else None
    if role == "message" and isinstance(reference, str):
        handle = core.inbox.message_handle(reference)
        return None if p.kind in ("key", "oauth", "connect") and can_read_conversation(core, p, handle) else MESSAGE_NOT_FOUND
    if role in ("conversation", "provider_ref") and isinstance(reference, str):
        reference = reference.strip()
        if role == "provider_ref" and not reference.startswith("h_"):
            block = next((b for b in core.blocks.values() if b.owns_ref(reference)
                          and (not name.startswith("capy_") or b.manifest.id == "capy")), None)
            if block is not None:
                if p.is_unrestricted_owner:
                    return None
                for row in core.store.q("SELECT handle FROM handles WHERE destination = ? AND ref = ? AND owner = ?",
                                        (block.name, reference, p.source)):
                    if can_read_conversation(core, p, core.inbox.handle(row["handle"])):
                        return None
            return CONVERSATION_NOT_FOUND
        handle = core.inbox.handle(reference)
        if name in ("capy_result", "capy_status") and handle is not None:
            block = core.blocks.get(handle["destination"])
            if block is None or block.manifest.id != "capy":
                return CONVERSATION_NOT_FOUND
        return None if can_read_conversation(core, p, handle) else CONVERSATION_NOT_FOUND

    scope = arguments.get("scope")
    if role == "memory":
        if isinstance(scope, str) and scope and not core.allows_memory(p, scope.strip().lower()):
            return MEMORY_NOT_FOUND
        namespace, key = arguments.get("namespace"), arguments.get("key")
        ident = reference if isinstance(reference, str) else None
        if ident or all(isinstance(value, str) and value for value in (scope, namespace, key)):
            try:
                scope = core.memory.scope_of(ident, scope=scope, namespace=namespace, key=key)
            except MemoryError_:
                return MEMORY_NOT_FOUND
            if not core.allows_memory(p, scope):
                return MEMORY_NOT_FOUND
    if role == "memory_scope":
        project = arguments.get("project")
        scopes = arguments.get("scopes")
        effective_project = project.strip().lower().removeprefix("project:") if isinstance(project, str) and project else ""
        if effective_project and not core.allows_memory(p, GENERAL if effective_project == GENERAL else f"project:{effective_project}"):
            return MEMORY_NOT_FOUND
        if isinstance(scopes, list) and scopes and all(isinstance(s, str) for s in scopes) and not granted_memory_scopes(core, p, scopes):
            return MEMORY_NOT_FOUND
        if not effective_project and not scopes and not default_memory_scopes(core, p):
            return MEMORY_NOT_FOUND
    return None


def can_read_project(p: Principal, project: Project) -> bool:
    return not project.private or p.allows_private_memory(project.scope)


def mutation_roles(core: Switchboard) -> dict[str, Role]:
    return {
        **MUTATION_ROLES,
        **{f"{block.name}_{suffix}": role for block in core.blocks.values()
           for suffix, role in (CHAT_ROLES if isinstance(block, ChatBlock) else BLOCK_ROLES.get(block.manifest.id, {})).items()},
    }


def read_roles(core: Switchboard) -> dict[str, ReadPolicy]:
    return {
        **READ_ROLES,
        **{f"{block.name}_{suffix}": role for block in core.blocks.values()
           for suffix, role in BLOCK_READ_ROLES.get(block.manifest.id, {}).items()},
    }
