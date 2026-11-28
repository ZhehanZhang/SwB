"""Owner-only memory archive and Git backup tools."""

from __future__ import annotations

import asyncio
import base64
import binascii
from typing import Any

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import BlobResourceContents, CallToolResult, EmbeddedResource, TextContent

from ...memory_archive import MAX_ARCHIVE_BYTES, MIME, export_zip, import_zip
from . import READ, WRITE


def register(mcp, core, who, sync) -> None:
    @mcp.tool(title="Export memory", description="Download active memory and its history as a zip file.", annotations=READ)
    async def memory_export(ctx: Context) -> CallToolResult:
        data, count, date = await asyncio.to_thread(sync, export_zip, core, who(ctx))
        return CallToolResult(content=[
            TextContent(type="text", text=f"Exported {count} memory entries."),
            EmbeddedResource(resource=BlobResourceContents(
                uri=f"swb://memory/export/{date}.zip", mime_type=MIME, blob=base64.b64encode(data).decode()
            )),
        ])

    @mcp.tool(title="Import memory", description="Import a base64-encoded SwB memory zip file.", annotations=WRITE)
    async def memory_import(ctx: Context, zip_base64: str, trust_owner: bool = False, overwrite: bool = False) -> dict[str, Any]:
        if len(zip_base64) > (MAX_ARCHIVE_BYTES + 2) // 3 * 4:
            raise ToolError("Use a memory archive up to 50 MB.")
        try:
            data = base64.b64decode(zip_base64, validate=True)
        except (ValueError, binascii.Error):
            raise ToolError("Use a base64-encoded memory zip file.") from None
        return await asyncio.to_thread(sync, import_zip, core, who(ctx), data, trust_owner=trust_owner, overwrite=overwrite)

    @mcp.tool(title="Check memory backup", description="Check Git backup status, pending changes and retries.", annotations=READ)
    async def memory_backup_status(ctx: Context) -> dict[str, Any]:
        return core.memory_git.status()

    @mcp.tool(title="Configure memory backup", description="Set the Git destination and get a link to save its credential.", annotations=WRITE)
    async def memory_backup_configure(
        ctx: Context, remote: str | None = None, branch: str | None = None, author_name: str | None = None,
        author_email: str | None = None, enabled: bool | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(sync, core.memory_git.configure, who(ctx), remote=remote, branch=branch,
                                       author_name=author_name, author_email=author_email, enabled=enabled, claim=True)

    @mcp.tool(title="Back up memory now", description="Start a Git backup now and reset the retry delay.", annotations=WRITE)
    async def memory_backup_now(ctx: Context) -> dict[str, Any]:
        return sync(core.memory_git.now, who(ctx))

    @mcp.tool(title="Restore memory from Git", description="Import memory from the configured Git branch or a commit.", annotations=WRITE)
    async def memory_backup_restore(
        ctx: Context, ref: str | None = None, trust_owner: bool = False, overwrite: bool = False,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(sync, core.memory_git.restore, who(ctx), ref, trust_owner=trust_owner, overwrite=overwrite)
