"""brainstem-mcp: your grail Brainstem as an MCP server.

The server is in brainstem_mcp.server (tools chat, job_status, capabilities over the Brainstem's unchanged
/chat and /health). This package is the one engine: an MCP distro (RAPPMCP is one) is a kernel pin plus
agent.py files pinned by sha256, dropped into the grail Brainstem's agents/ folder and served from here.

Tool naming rule, shared with RAPPMCP. Every tool name matches TOOL_NAME, and no agent tool may take a
RESERVED name: those belong to the Brainstem itself (setup is the one tool offered before setup has run).
"""
import re

__version__ = "0.4.0"

TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
RESERVED_TOOL_NAMES = frozenset({"chat", "job_status", "capabilities", "setup"})
