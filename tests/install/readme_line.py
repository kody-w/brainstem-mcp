"""Run the README's install line exactly as a user's MCP app would, and list the tools.

Used by CI on clean machines. Pass the ref to install (a branch or commit) as the first argument.
"""
import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REF = sys.argv[1] if len(sys.argv) > 1 else "main"
EXPECTED = {"chat", "job_status", "capabilities"}


async def main() -> None:
    server = StdioServerParameters(
        command="uvx",
        args=["--from", f"git+https://github.com/kody-w/brainstem-mcp@{REF}", "brainstem-mcp"],
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
    print("tools:", sorted(names))
    missing = EXPECTED - names
    if missing:
        sys.exit(f"the install line started, but these tools are missing: {sorted(missing)}")
    print("the README install line works on this machine")


asyncio.run(asyncio.wait_for(main(), timeout=300))
