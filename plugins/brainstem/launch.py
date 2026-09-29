#!/usr/bin/env python3
"""Start the Brainstem MCP bridge from its own small environment.

    python3 launch.py           start the bridge over stdio (what Claude Code runs); before setup it
                                offers a single `setup` tool instead
    python3 launch.py --setup   build the environment if needed and report readiness as JSON

Standard library only. The environment lives outside the plugin, in ~/.cache/brainstem-mcp
(BRAINSTEM_MCP_HOME overrides), and is built with pip, so it respects whatever package index
the machine is configured for. Nothing is written to stdout in bridge mode except MCP traffic.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path(os.getenv("BRAINSTEM_MCP_HOME") or Path.home() / ".cache" / "brainstem-mcp")
ENV = HOME / "venv"
REQUIREMENTS = ["mcp>=2.0,<3", "requests>=2.31"]
MIN_PYTHON = (3, 10)
BRAINSTEM_URL = (os.getenv("BRAINSTEM_URL") or "http://127.0.0.1:7071").rstrip("/")
INSTALL_HINT = "curl -fsSL https://kody-w.github.io/rapp-installer/install.sh | bash"


def _env_python() -> Path:
    return ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _newer_python() -> str | None:
    """A Python on PATH new enough for the bridge, for machines whose python3 is too old."""
    for name in ("python3.14", "python3.13", "python3.12", "python3.11", "python3.10"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _env_ready() -> bool:
    python = _env_python()
    return python.exists() and subprocess.run(
        [str(python), "-c", "import mcp.server.mcpserver, requests"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def _build_env(log) -> None:
    venv.create(ENV, with_pip=True, clear=True)
    base = [str(_env_python()), "-m", "pip", "install", "-q", "--disable-pip-version-check"]
    # A second attempt without the cache: package proxies sometimes serve a truncated file.
    for extra in ([], ["--no-cache-dir"]):
        if subprocess.run(base + extra + REQUIREMENTS, stdout=log, stderr=log).returncode == 0:
            return
    raise RuntimeError("pip could not install the bridge's two packages (mcp, requests). "
                       "Check this machine can reach its package index, then run setup again.")


def _brainstem() -> dict:
    try:
        with urllib.request.urlopen(f"{BRAINSTEM_URL}/health", timeout=10) as reply:
            health = json.loads(reply.read())
    except (urllib.error.URLError, OSError, ValueError):
        return {"reachable": False, "url": BRAINSTEM_URL}
    if not isinstance(health, dict):
        return {"reachable": False, "url": BRAINSTEM_URL}
    return {"reachable": True, "url": BRAINSTEM_URL, "version": health.get("version"),
            "signedIn": health.get("status") == "ok", "model": health.get("model"),
            "agents": len(health.get("agents") or [])}


def readiness() -> dict:
    report = {"ready": False, "python": sys.version.split()[0], "bridge": None, "brainstem": None,
              "actionsTaken": [], "nextSteps": []}
    if sys.version_info < MIN_PYTHON:
        report["bridge"] = f"needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+; this is {report['python']}"
        report["nextSteps"].append("Install Python 3.11 or newer (the Brainstem needs it too), then run setup again.")
        return report
    if _env_ready():
        report["bridge"] = f"ready ({ENV})"
    else:
        try:
            _build_env(sys.stderr)
            report["bridge"] = f"ready ({ENV})"
            report["actionsTaken"].append("Built the bridge environment (mcp, requests).")
        except (RuntimeError, OSError) as error:
            report["bridge"] = f"not ready: {error}"
            report["nextSteps"].append("Fix the package install, then run /brainstem:setup again.")
    brainstem = report["brainstem"] = _brainstem()
    if not brainstem["reachable"]:
        report["nextSteps"].append(f"No Brainstem answered at {BRAINSTEM_URL}. Install or start it: {INSTALL_HINT}")
    elif not brainstem["signedIn"]:
        report["nextSteps"].append(f"The Brainstem is running but not signed in. Open {BRAINSTEM_URL} and sign in with GitHub.")
    report["ready"] = report["bridge"].startswith("ready") and brainstem["reachable"] and brainstem["signedIn"]
    if report["ready"] and report["actionsTaken"]:
        report["nextSteps"].append("Run /reload-plugins so Claude Code starts the bridge.")
    return report


def setup() -> int:
    report = readiness()
    print(json.dumps(report, indent=2))
    return 0 if report["ready"] else 1


SETUP_TOOL = {
    "name": "setup",
    "description": ("The Brainstem bridge is not set up yet. Call this once: it prepares the bridge (about a "
                    "minute) and checks the Brainstem is running and signed in. Then the user runs /reload-plugins."),
    "inputSchema": {"type": "object", "properties": {}},
}


def setup_server() -> int:
    """Before setup there is no `mcp` package, so answer MCP over stdio with the standard library and offer one
    tool, `setup`. The host connects cleanly (no cached failure) and can finish setup itself."""
    def send(message):
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        method, ident = message.get("method"), message.get("id")
        if ident is None:
            continue
        if method == "initialize":
            version = (message.get("params") or {}).get("protocolVersion") or "2025-06-18"
            send({"jsonrpc": "2.0", "id": ident, "result": {
                "protocolVersion": version, "capabilities": {"tools": {}},
                "serverInfo": {"name": "ai-brainstem", "version": "setup"},
                "instructions": "Not set up yet. Call `setup`, then ask the user to run /reload-plugins."}})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": ident, "result": {}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": ident, "result": {"tools": [SETUP_TOOL]}})
        elif method == "tools/call" and (message.get("params") or {}).get("name") == "setup":
            report = readiness()
            if report["ready"] and "Run /reload-plugins" not in " ".join(report["nextSteps"]):
                report["nextSteps"].append("Run /reload-plugins so Claude Code starts the bridge.")
            send({"jsonrpc": "2.0", "id": ident, "result": {
                "content": [{"type": "text", "text": json.dumps(report, indent=2)}], "isError": not report["ready"]}})
        else:
            send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"unknown method {method}"}})
    return 0


def serve() -> int:
    # Building takes longer than a host waits for a server to start, so never build here.
    if not _env_ready():
        return setup_server()
    command = [str(_env_python()), str(HERE / "mcp_server.py"), *sys.argv[1:]]
    if os.name == "nt":
        return subprocess.call(command)
    os.execv(command[0], command)
    return 0


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        newer = _newer_python()
        if newer:
            os.execv(newer, [newer, str(Path(__file__).resolve()), *sys.argv[1:]])
        if "--setup" not in sys.argv:
            print(f"brainstem-mcp needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+", file=sys.stderr)
            return 1
    return setup() if "--setup" in sys.argv else serve()


if __name__ == "__main__":
    sys.exit(main())
