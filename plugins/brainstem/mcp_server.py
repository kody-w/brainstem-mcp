#!/usr/bin/env python3
"""MCP bridge: lets other AIs (Copilot CLI, Claude Code, ...) talk to a running AI Brainstem.

Three tools, all thin clients of the unchanged kernel (the grail is the kernel; this is userland):

    chat(user_input, session_id?, wait?)   POST /chat; per-session history is kept here because
                                           the kernel's /chat is stateless
    job_status(session_id)                 result of a chat started with wait=false; background
                                           turns are kept here because the kernel's /chat is synchronous
    capabilities()                         GET /health: status, model and loaded agents

Every call lands in the Brainstem's own /chat, so its agents (including Twins) do the work.

Claude Code starts this through launch.py (see the repo README); it can also run on its own:
Run (stdio):  python mcp_server.py
Run (HTTP):   python mcp_server.py --http 7072
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

mcp = MCPServer(
    "ai-brainstem",
    instructions=("A local AI Brainstem. Call `chat` in plain language; it chooses its own agents, "
                  "including its twins (use their names). Reuse `session_id` to continue a conversation. "
                  "For work that may take minutes, call `chat` with wait=false and poll `job_status`."),
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


@mcp.tool()
def chat(user_input: str, session_id: str | None = None, wait: bool = True) -> str:
    """Send one message to the AI Brainstem and return its answer as JSON:
    response, session_id, model, agent_logs (the agents it used). A failure is returned as a
    tool error with a plain explanation. Pass the returned session_id to continue the conversation.
    wait=false: for work that may take minutes. Returns {status: running, session_id} at once;
    poll job_status(session_id) for the answer. One message at a time per session."""
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
