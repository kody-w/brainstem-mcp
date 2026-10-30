"""MCP bridge: lets other AIs (Claude Code, Codex, Antigravity, any MCP host) talk to a running AI Brainstem.

Three tools, all thin clients of the unchanged kernel (the grail is the kernel; this is userland):

    chat(user_input, session_id?, wait?)   POST /chat; per-session history is kept here because
                                           the kernel's /chat is stateless
    job_status(session_id)                 result of a chat started with wait=false; background
                                           turns are kept here because the kernel's /chat is synchronous
    capabilities()                         GET /health: status, model and loaded agents

Every call lands in the Brainstem's own /chat, so its agents (including Twins) do the work.

Ways to start it (stdio unless --http):
    brainstem-mcp                            the console script, after a pip or uvx install
    python -m brainstem_mcp                  the same, from a checkout (plugins/brainstem on the path)
    python plugins/brainstem/mcp_server.py   the old entry point, kept working
    python plugins/brainstem/launch.py       what the Claude Code plugin runs (builds its own environment)
    brainstem-mcp --http 7072                streamable HTTP instead of stdio
    brainstem-mcp --agents [DIR]             also one tool per agent in the Brainstem's agents folder (engine.py)
    brainstem-mcp --distro distro.json       also one tool per agent a distro pins by sha256 (distro.py)
Environment:  BRAINSTEM_URL (default http://127.0.0.1:7071), BRAINSTEM_SECRET (LAN mode only),
              BRAINSTEM_MCP_TIMEOUT (seconds a waiting chat waits, default 240),
              BRAINSTEM_MCP_JOB_TIMEOUT (seconds a background chat may run, default 3600).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid

import requests
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from brainstem_mcp import __version__

BRAINSTEM_URL = (os.getenv("BRAINSTEM_URL") or "http://127.0.0.1:7071").rstrip("/")
BRAINSTEM_SECRET = os.getenv("BRAINSTEM_SECRET", "")
CHAT_TIMEOUT = float(os.getenv("BRAINSTEM_MCP_TIMEOUT", "240"))
JOB_TIMEOUT = float(os.getenv("BRAINSTEM_MCP_JOB_TIMEOUT", "3600"))
HISTORY_MESSAGES = 20
HISTORY_CHARS = 24000
FINISHED_JOBS_KEPT = 50

_histories: dict[str, list[dict]] = {}
_busy: set[str] = set()
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _loaded_agents() -> list[str]:
    """The agents the Brainstem has loaded right now, read once at startup. A host only hands work to
    the Brainstem when it can see what the Brainstem is for; an empty list keeps the generic wording."""
    try:
        agents = requests.get(f"{BRAINSTEM_URL}/health", headers=_headers(), timeout=3).json().get("agents") or []
    except Exception:
        return []
    return [a for a in agents if isinstance(a, str) and a not in ("BasicAgent", "ContextMemory", "ManageMemory")]


def _agents_sentence(agents: list[str]) -> str:
    if not agents:
        return ""
    return (f" Its agents: {', '.join(agents)}. When a request matches one of them, send it to `chat` "
            "instead of doing the work yourself: the agent runs it as code and returns only the answer.")


def _headers() -> dict:
    headers = {"content-type": "application/json"}
    if BRAINSTEM_SECRET:
        headers["X-Brainstem-Secret"] = BRAINSTEM_SECRET
    return headers


AGENTS = _loaded_agents()
mcp = MCPServer(
    "ai-brainstem",
    instructions=("A local AI Brainstem. Call `chat` in plain language; it chooses its own agents, "
                  "including its twins (use their names)." + _agents_sentence(AGENTS) +
                  " Reuse `session_id` to continue a conversation. "
                  "For work that may take minutes, call `chat` with wait=false and poll `job_status`."),
)


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


def _turn(user_input: str, session_id: str, timeout: float) -> dict:
    """One conversation turn. The caller holds the session in _busy, so history cannot interleave."""
    with _lock:
        history = list(_histories.get(session_id, []))
    body = {"user_input": user_input, "session_id": session_id, "conversation_history": history}
    data = _call("POST", "/chat", timeout, json=body)
    answer = data.get("response")
    if not isinstance(answer, str):
        raise ToolError("The AI Brainstem replied without an answer (no `response` field).")
    with _lock:
        _histories[session_id] = _trim(history + [{"role": "user", "content": user_input},
                                                  {"role": "assistant", "content": answer}])
    result = {key: data.get(key) for key in ("response", "model", "agent_logs") if data.get(key)}
    result["session_id"] = session_id
    return result


def _claim(session_id: str) -> None:
    with _lock:
        if session_id in _busy:
            raise ToolError(f"The AI Brainstem is still answering the previous message in session {session_id}. "
                            "Wait for it (job_status, if it was started with wait=false), then send again.")
        _busy.add(session_id)


def _release(session_id: str) -> None:
    with _lock:
        _busy.discard(session_id)


def _run_job(user_input: str, session_id: str) -> None:
    try:
        outcome = {"status": "done", "result": _turn(user_input, session_id, JOB_TIMEOUT)}
    except ToolError as error:
        outcome = {"status": "error", "error": str(error)}
    except Exception as error:  # a background thread must always record how it ended
        outcome = {"status": "error", "error": f"The background turn failed: {error}"}
    with _lock:
        _jobs[session_id].update(outcome, finished_at=time.time())
        finished = sorted((job["finished_at"], sid) for sid, job in _jobs.items() if job.get("finished_at"))
        for _, old in finished[:-FINISHED_JOBS_KEPT]:
            _jobs.pop(old, None)
        _busy.discard(session_id)


CHAT_DESCRIPTION = ("Send one message to the AI Brainstem and return its answer as JSON:\n"
                    "response, session_id, model, agent_logs (the agents it used)." + _agents_sentence(AGENTS) + "\n"
                    "A failure is returned as a tool error with a plain explanation. Pass the returned session_id "
                    "to continue the conversation.\nwait=false: for work that may take minutes. Returns "
                    "{status: running, session_id} at once; poll job_status(session_id) for the answer. "
                    "One message at a time per session.")


@mcp.tool(description=CHAT_DESCRIPTION)
def chat(user_input: str, session_id: str | None = None, wait: bool = True) -> str:
    session_id = session_id or f"mcp-{uuid.uuid4().hex[:12]}"
    _claim(session_id)
    if not wait:
        with _lock:
            _jobs[session_id] = {"status": "running", "started_at": time.time()}
        threading.Thread(target=_run_job, args=(user_input, session_id), daemon=True).start()
        return json.dumps({"status": "running", "session_id": session_id})
    try:
        return json.dumps(_turn(user_input, session_id, CHAT_TIMEOUT), ensure_ascii=False)
    finally:
        _release(session_id)


@mcp.tool()
def job_status(session_id: str) -> str:
    """Status of a chat started with wait=false: running (with seconds so far), done (with the
    answer in `result`) or error (with a plain explanation)."""
    with _lock:
        job = dict(_jobs.get(session_id) or {})
    if not job:
        raise ToolError(f"No background chat for session {session_id}. Start one with chat(..., wait=false).")
    if job["status"] == "running":
        return json.dumps({"status": "running", "session_id": session_id,
                           "seconds": round(time.time() - job["started_at"])})
    report = {"status": job["status"], "session_id": session_id,
              "seconds": round(job["finished_at"] - job["started_at"])}
    report.update({key: job[key] for key in ("result", "error") if key in job})
    return json.dumps(report, ensure_ascii=False)


@mcp.tool()
def capabilities() -> str:
    """What this AI Brainstem can do right now: status, version, model and loaded agents."""
    health = _call("GET", "/health", 30)
    return json.dumps({key: health.get(key) for key in ("status", "version", "model", "agents", "quarantined")})


USAGE = """usage: brainstem-mcp [--http PORT]
       brainstem-mcp --agents [DIR] [--distro MANIFEST] [--timeout S] [--scan-timeout S] [--kernel PATH]
       brainstem-mcp --distro MANIFEST [...]

Serve your local AI Brainstem as an MCP server: chat, job_status, capabilities.
It talks only to the Brainstem's own /chat and /health at BRAINSTEM_URL (default http://127.0.0.1:7071).

  (no options)        stdio, what every MCP host starts
  --http PORT         streamable HTTP on 127.0.0.1:PORT instead (BRAINSTEM_MCP_HOST changes the address)
  --agents [DIR]      also serve each agent in your Brainstem's agents folder as its own tool
                      (default ~/.brainstem/src/rapp_brainstem/agents). Each call runs in its own
                      short-lived process with a timeout. The tool list follows the folder.
  --distro MANIFEST   also serve a distro: a kernel pin plus agent files pinned by sha256. A file
                      whose bytes do not match its pin is refused.
  --timeout S         seconds one agent call may run before it is stopped (default 120)
  --scan-timeout S    seconds one agent file may take to load before it is skipped (default 10)
  --kernel PATH       use this basic_agent.py instead of the pinned one
  --version           print the version
"""

DEFAULT_AGENTS = "~/.brainstem/src/rapp_brainstem/agents"


def _agent_options(argv: list[str]):
    import argparse

    parser = argparse.ArgumentParser(prog="brainstem-mcp", add_help=False)
    parser.add_argument("--agents", nargs="?", const=DEFAULT_AGENTS, default=None)
    parser.add_argument("--distro", default=None)
    parser.add_argument("--timeout", type=float, default=float(os.getenv("BRAINSTEM_MCP_CALL_TIMEOUT", "120")))
    parser.add_argument("--scan-timeout", type=float, default=float(os.getenv("BRAINSTEM_MCP_SCAN_TIMEOUT", "10")))
    parser.add_argument("--kernel", default=None)
    parser.add_argument("--poll", type=float, default=1.0)
    return parser.parse_args(argv)


def serve_agents(argv: list[str]) -> int:
    """chat, job_status and capabilities, plus one tool per agent file, over stdio."""
    if "--http" in argv:
        print("brainstem-mcp: --agents and --distro serve over stdio only, for now.", file=sys.stderr)
        return 2
    try:
        options = _agent_options(argv)
    except SystemExit:
        print(USAGE, end="", file=sys.stderr)
        return 2
    sys.dont_write_bytecode = True  # never write into the kernel install or the agents' folder
    if options.kernel:
        os.environ["BRAINSTEM_MCP_KERNEL"] = os.path.abspath(os.path.expanduser(options.kernel))
    import anyio
    import mcp_types as types

    from brainstem_mcp import engine
    from brainstem_mcp.distro import DistroError
    from mcp.shared.exceptions import MCPError

    class BrainstemTools(engine.BaseTools):
        names = frozenset({"chat", "job_status", "capabilities"})
        instructions = mcp.instructions

        async def tools(self):
            return await mcp.list_tools()

        async def call(self, name, arguments):
            try:
                return await mcp.call_tool(name, arguments)
            except MCPError:
                raise
            except Exception as error:  # ToolError and the rest: a failed call in plain words
                return types.CallToolResult(content=[types.TextContent(text=str(error))], is_error=True)

    try:
        served = engine.Engine(
            os.path.expanduser(options.agents) if options.agents else None,
            timeout=options.timeout, poll=options.poll, scan_timeout=options.scan_timeout,
            distro=options.distro, base=BrainstemTools(), name="ai-brainstem", version=__version__,
        )
    except (FileNotFoundError, DistroError, ValueError) as error:
        print(f"brainstem-mcp: {error}", file=sys.stderr)
        return 2
    try:
        anyio.run(engine.serve_stdio, served)
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: list[str]) -> int:
    if "-h" in argv or "--help" in argv:
        print(USAGE, end="")
        return 0
    if "--version" in argv:
        print(f"brainstem-mcp {__version__}")
        return 0
    if "--agents" in argv or "--distro" in argv:
        return serve_agents(argv)
    if "--http" in argv:
        index = argv.index("--http")
        port = int(argv[index + 1]) if len(argv) > index + 1 else 7072
        mcp.run(transport="streamable-http", host=os.getenv("BRAINSTEM_MCP_HOST", "127.0.0.1"), port=port)
    else:
        mcp.run()
    return 0


def cli() -> int:
    """The `brainstem-mcp` console script."""
    return main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(cli())
