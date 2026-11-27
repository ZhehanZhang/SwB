from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from ...chat import ChatBlock

if TYPE_CHECKING:
    from ...core import Switchboard

Role = Literal["owner", "self", "grant", "route", "setup"]

MUTATION_ROLES: dict[str, Role] = {
    "space_mode": "owner",
    "project_create": "owner",
    "project_update": "owner",
    "project_sync": "owner",
    "routes_set": "route",
    "routes_remove": "route",
    "memory_write": "grant",
    "memory_delete": "grant",
    "memory_move": "grant",
    "memory_promote": "grant",
    "login_link": "owner",
    "visibility_set": "owner",
    "connect_code": "owner",
    "connect_revoke": "owner",
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
}

AUTH_REQUIRED = "Sign in to call tools."
OWNER_REQUIRED = "Use the owner's sign-in to call this tool."
ROUTE_REQUIRED = "Use a route for your own agent or sign in as the owner."
CONVERSATION_REQUIRED = "Choose a conversation for your own agent."
SCOPE_REQUIRED = "Choose a project granted to this key."
TARGET_REQUIRED = "Choose a target granted to this key."
TOOL_REQUIRED = "Use a key granted this tool."
POLICY_REQUIRED = "Use a tool with a SwB write policy."
MODE_REQUIRED = "Use an action allowed by the conversation mode."

REFUSAL_DETAILS = frozenset({
    AUTH_REQUIRED, OWNER_REQUIRED, ROUTE_REQUIRED, CONVERSATION_REQUIRED, SCOPE_REQUIRED, TARGET_REQUIRED, TOOL_REQUIRED,
    POLICY_REQUIRED, MODE_REQUIRED,
})


def mutation_roles(core: Switchboard) -> dict[str, Role]:
    return {
        **MUTATION_ROLES,
        **{f"{block.name}_{suffix}": role for block in core.blocks.values()
           for suffix, role in (CHAT_ROLES if isinstance(block, ChatBlock) else BLOCK_ROLES.get(block.manifest.id, {})).items()},
    }
