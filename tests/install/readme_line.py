"""Run the README's install line exactly as a user's MCP app would, and list the tools.

Then the same line with --agents on a folder of example agents: list them and call one. That proves
the engine (per-call workers, the pinned kernel fetched and checked) works on a clean machine with
no Brainstem. Used by CI. Pass the ref to install (a branch or commit) as the first argument.
"""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REF = sys.argv[1] if len(sys.argv) > 1 else "main"
EXPECTED = {"chat", "job_status", "capabilities"}
EXAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine", "fixtures", "examples")


def line(*extra):
    return StdioServerParameters(
        command="uvx",
        args=["--from", f"git+https://github.com/kody-w/brainstem-mcp@{REF}", "brainstem-mcp", *extra],
    )


async def main() -> None:
    async with stdio_client(line()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
    print("tools:", sorted(names))
    missing = EXPECTED - names
    if missing:
        sys.exit(f"the install line started, but these tools are missing: {sorted(missing)}")
    print("the README install line works on this machine")

    async with stdio_client(line("--agents", os.path.abspath(EXAMPLES))) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [tool.name for tool in (await session.list_tools()).tools]
            print("tools with --agents:", names)
            if names[:3] != ["chat", "job_status", "capabilities"] or "Echo" not in names:
                sys.exit("--agents started, but the tool list is not chat, job_status, capabilities, then the agents")
            result = await session.call_tool("Echo", {"text": "clean machine"})
            print("Echo said:", result.content[0].text)
            if result.is_error or result.content[0].text != "clean machine":
                sys.exit("--agents listed Echo, but calling it failed")
    print("--agents works on this machine")


asyncio.run(asyncio.wait_for(main(), timeout=300))
