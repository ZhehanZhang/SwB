from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field, StrictBool

if TYPE_CHECKING:
    from . import WebhookBlock

Name = Annotated[str, Field(description="Webhook name with 1 to 40 lowercase letters, digits or hyphens.")]
Scheme = Literal["stripe", "linear", "sentry", "standard", "svix", "hmac-sha256", "hmac-sha1", "bearer", "path"]
AllowPrivate = Annotated[StrictBool | None, Field(description="Permit private HTTPS receivers and alternate ports.")]
Senders = Annotated[list[str] | None, Field(description="Grant outbound access to agent addresses such as agent:claude.")]


def register(block: WebhookBlock, mcp: MCPServer, kit: Any) -> None:
    bname = block.name

    @mcp.tool(name=f"{bname}_create", title="Create a webhook", annotations=kit.WRITE)
    async def webhook_create(
        ctx: Context,
        name: Name,
        direction: Annotated[Literal["in", "out"], Field(description="Receive provider events or send messages to a receiver.")] = "in",
        verify: Annotated[Scheme | None, Field(description="Required verification scheme for inbound events.")] = None,
        to: Annotated[str | None, Field(description="Inbound event target address from targets.")] = None,
        project: Annotated[str | None, Field(description="Project slug for inbound events.")] = None,
        header: Annotated[str | None, Field(description="Signature header for generic HMAC.")] = None,
        prefix: Annotated[str | None, Field(description="Signature prefix for generic HMAC, such as sha256=.")] = None,
        encoding: Annotated[Literal["hex", "base64"] | None, Field(description="Signature encoding for generic HMAC.")] = None,
        tolerance_s: Annotated[int | None, Field(description="Maximum timestamp difference in seconds for Stripe, Linear or Standard Webhooks.")] = None,
        fields: Annotated[list[str] | None, Field(description="Dotted paths to include in the inbound message.")] = None,
        template: Annotated[str | None, Field(description="One-line summary with placeholders such as {data.object.id}.")] = None,
        event_field: Annotated[str | None, Field(description="Dotted path to the inbound event type.")] = None,
        allow_private: AllowPrivate = None,
        senders: Senders = None,
    ) -> dict[str, Any]:
        """Create a webhook and get a private credential link."""
        return await kit.invoke(ctx, bname, "create", name=name, direction=direction, verify=verify, to=to, project=project,
                                header=header, prefix=prefix, encoding=encoding, tolerance_s=tolerance_s, fields=fields,
                                template=template, event_field=event_field, allow_private=allow_private, senders=senders)

    @mcp.tool(name=f"{bname}_list", title="List webhooks", annotations=kit.READ)
    async def webhook_list(ctx: Context, include_revoked: Annotated[bool, Field(description="Include revoked webhooks.")] = False) -> dict[str, Any]:
        """List webhook addresses, targets, event counts and credential state."""
        return await kit.invoke(ctx, bname, "list", include_revoked=include_revoked)

    @mcp.tool(name=f"{bname}_rotate", title="Rotate a webhook secret", annotations=kit.WRITE)
    async def webhook_rotate(ctx: Context, name: Name,
                             grace_s: Annotated[int, Field(description="Seconds to keep accepting the old secret, up to seven days.")] = 0,
                             allow_private: AllowPrivate = None, senders: Senders = None) -> dict[str, Any]:
        """Get a private link to replace a webhook secret."""
        return await kit.invoke(ctx, bname, "rotate", name=name, grace_s=grace_s, allow_private=allow_private, senders=senders)

    @mcp.tool(name=f"{bname}_revoke", title="Revoke a webhook", annotations=kit.DELETE)
    async def webhook_revoke(ctx: Context, name: Name) -> dict[str, Any]:
        """Revoke a webhook, delete its secrets and close its claim links."""
        return await kit.invoke(ctx, bname, "revoke", name=name)

    @mcp.tool(name=f"{bname}_test", title="Test a webhook", annotations=kit.SEND)
    async def webhook_test(ctx: Context, name: Name) -> dict[str, Any]:
        """Send a signed sample event through a webhook."""
        return await kit.invoke(ctx, bname, "test", name=name)
