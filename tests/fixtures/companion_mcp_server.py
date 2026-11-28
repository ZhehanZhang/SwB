from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from mcp.types import INTERNAL_ERROR, CallToolResult, ImageContent, ListToolsResult, TextContent, Tool

SENTINEL = "companion-fixture-private-diagnostic"
ECHO_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string", "description": "Text to echo."}},
    "required": ["text"],
}


def make_server(transport: str, events: Path | None = None, *, large_catalogue: bool = False) -> Server:
    def record(event: str) -> None:
        if events is not None:
            with events.open("a", encoding="utf-8") as stream:
                print(event, file=stream)

    async def list_tools(ctx, params):
        if params is not None and params.cursor == "more":
            return ListToolsResult(tools=[
                Tool(name=name, description="x" * (512 * 1024) if large_catalogue else "", input_schema={"type": "object"})
                for name in ("report_error", "fail", "blob", "image", "crash", "slow")
            ])
        return ListToolsResult(
            tools=[Tool(name="echo", description="Echo text on this fixture.", input_schema=ECHO_SCHEMA)],
            next_cursor="more" if events is not None else None,
        )

    async def call_tool(ctx, params):
        record(params.name)
        args = params.arguments or {}
        if params.name == "fail":
            raise MCPError(INTERNAL_ERROR, args.get("text", SENTINEL))
        if params.name == "crash" and transport == "stdio":
            os._exit(13)
        if params.name == "slow":
            await asyncio.sleep(60)
        if params.name == "report_error":
            return CallToolResult(content=[TextContent(type="text", text=args.get("text", SENTINEL))], is_error=True)
        if params.name == "blob":
            return CallToolResult(content=[TextContent(type="text", text=args.get("character", "x") * args["size"])])
        if params.name == "image":
            return CallToolResult(content=[ImageContent(type="image", data="YWJj", mime_type="image/png")])
        text = args["text"]
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content={"text": text, "transport": transport},
        )

    return Server("companion-fixture", on_list_tools=list_tools, on_call_tool=call_tool)


async def main(events: Path, large_catalogue: bool) -> None:
    with events.open("a", encoding="utf-8") as stream:
        print("start", file=stream)
    print(SENTINEL, file=sys.stderr, flush=True)
    server = make_server("stdio", events, large_catalogue=large_catalogue)
    try:
        async with stdio_server() as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())
    finally:
        with events.open("a", encoding="utf-8") as stream:
            print("stop", file=stream)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), "--large-catalogue" in sys.argv[2:]))
