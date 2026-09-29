#!/usr/bin/env python3
"""MCP bridge: lets other AIs (Copilot CLI, Claude Code, ...) talk to a running AI Brainstem.

Two tools, both thin clients of the unchanged kernel:

    chat(user_input, session_id?)   POST /chat; per-session history is kept here because
                                    the kernel's /chat is stateless
    capabilities()                  GET /health: status, model and loaded agents

Every call lands in the Brainstem's own /chat, so its agents (including Twins) do the work.

Claude Code starts this through launch.py (see the repo README); it can also run on its own:
Run (stdio):  python mcp_server.py
Run (HTTP):   python mcp_server.py --http 7072
Environment:  BRAINSTEM_URL (default http://127.0.0.1:7071), BRAINSTEM_SECRET (LAN mode only).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import uuid

import requests
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

BRAINSTEM_URL = (os.getenv("BRAINSTEM_URL") or "http://127.0.0.1:7071").rstrip("/")
BRAINSTEM_SECRET = os.getenv("BRAINSTEM_SECRET", "")
CHAT_TIMEOUT = float(os.getenv("BRAINSTEM_MCP_TIMEOUT", "240"))
HISTORY_MESSAGES = 20
HISTORY_CHARS = 24000

_histories: dict[str, list[dict]] = {}
_lock = threading.Lock()

mcp = MCPServer(
    "ai-brainstem",
    instructions=("A local AI Brainstem. Call `chat` in plain language; it chooses its own agents, "
                  "including its twins (use their names). Reuse `session_id` to continue a conversation."),
)


def _headers() -> dict:
    headers = {"content-type": "application/json"}
    if BRAINSTEM_SECRET:
        headers["X-Brainstem-Secret"] = BRAINSTEM_SECRET
    return headers


def _call(method: str, path: str, timeout: float, **kwargs) -> dict:
    """One request to the Brainstem. Every failure raises ToolError, so the host sees a failed call
    with a plain sentence, never a success carrying an error string or a JSON parser message."""
    try:
        reply = requests.request(method, f"{BRAINSTEM_URL}{path}", headers=_headers(), timeout=timeout, **kwargs)
    except requests.Timeout:
        raise ToolError(f"The AI Brainstem at {BRAINSTEM_URL} did not answer within {timeout:.0f} seconds.")
    except requests.RequestException:
        raise ToolError(f"The AI Brainstem is not running at {BRAINSTEM_URL}. Start it, then try again.")
    try:
        data = reply.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise ToolError(f"{BRAINSTEM_URL} answered HTTP {reply.status_code} but not as an AI Brainstem "
                        "(no JSON). Check BRAINSTEM_URL points at a running Brainstem.")
    if reply.status_code != 200 or data.get("error"):
        reason = data.get("error") or "no reason given"
        raise ToolError(f"The AI Brainstem refused the request (HTTP {reply.status_code}): {reason}")
    return data


def _trim(history: list[dict]) -> list[dict]:
    history = history[-HISTORY_MESSAGES:]
    while history and sum(len(item["content"]) for item in history) > HISTORY_CHARS:
        history = history[1:]
    return history


@mcp.tool()
def chat(user_input: str, session_id: str | None = None) -> str:
    """Send one message to the AI Brainstem and return its answer as JSON:
    response, session_id, model, agent_logs (the agents it used). A failure is returned as a
    tool error with a plain explanation. Pass the returned session_id to continue the conversation."""
    session_id = session_id or f"mcp-{uuid.uuid4().hex[:12]}"
    with _lock:
        history = list(_histories.get(session_id, []))
    body = {"user_input": user_input, "session_id": session_id, "conversation_history": history}
    data = _call("POST", "/chat", CHAT_TIMEOUT, json=body)
    answer = data.get("response")
    if not isinstance(answer, str):
        raise ToolError("The AI Brainstem replied without an answer (no `response` field).")
    with _lock:
        _histories[session_id] = _trim(history + [{"role": "user", "content": user_input},
                                                  {"role": "assistant", "content": answer}])
    result = {key: data.get(key) for key in ("response", "model", "agent_logs") if data.get(key)}
    result["session_id"] = session_id
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def capabilities() -> str:
    """What this AI Brainstem can do right now: status, version, model and loaded agents."""
    health = _call("GET", "/health", 30)
    return json.dumps({key: health.get(key) for key in ("status", "version", "model", "agents", "quarantined")})


def main(argv: list[str]) -> int:
    if "--http" in argv:
        index = argv.index("--http")
        port = int(argv[index + 1]) if len(argv) > index + 1 else 7072
        mcp.run(transport="streamable-http", host=os.getenv("BRAINSTEM_MCP_HOST", "127.0.0.1"), port=port)
    else:
        mcp.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
