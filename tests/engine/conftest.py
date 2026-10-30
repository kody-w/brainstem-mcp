"""Start the real brainstem-mcp server with --agents (or --distro) over stdio, with the official MCP client.

BRAINSTEM_URL points at a closed port, so no real Brainstem is ever called: chat, job_status and
capabilities are listed beside the agent tools, and the tests here look at the agent tools.
"""
import os
import pathlib
import sys
from contextlib import asynccontextmanager

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO = pathlib.Path(__file__).resolve().parents[2]
ROOT = REPO / "plugins" / "brainstem"  # the folder brainstem_mcp lives in
HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
EXAMPLES = FIXTURES / "examples"
BASE_TOOLS = ("chat", "job_status", "capabilities")
sys.path.insert(0, str(ROOT))


@pytest.fixture
def anyio_backend():
    return "asyncio"


def server_env(extra=None):
    """The full environment (Windows needs SYSTEMROOT to open a socket), pointed at no Brainstem."""
    return {**os.environ, "PYTHONPATH": str(ROOT), "BRAINSTEM_URL": "http://127.0.0.1:1", **(extra or {})}


@asynccontextmanager
async def connect(path, *flags, env=None, on_message=None, errlog=None, distro=None):
    """Serve path (a folder or file) with --agents, or a manifest with --distro.

    Pass an open file as errlog to keep the server's log lines."""
    serve = (["--agents", str(path)] if path is not None else []) + (["--distro", str(distro)] if distro else [])
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "brainstem_mcp", *serve, *flags],
        env=server_env(env),
        cwd=str(REPO),
    )
    kwargs = {"message_handler": on_message} if on_message else {}
    with open(os.devnull, "w") as quiet:
        async with stdio_client(params, errlog=errlog or quiet) as (read, write):
            async with ClientSession(read, write, **kwargs) as session:
                init = await session.initialize()
                yield session, init


def agent_tools(result):
    """The agent tools in a tools/list result, without the Brainstem's own three."""
    tools = result.tools
    assert [t.name for t in tools[:3]] == list(BASE_TOOLS), "chat, job_status and capabilities come first"
    return tools[3:]


def text_of(result):
    return "".join(block.text for block in result.content if getattr(block, "type", "") == "text")
