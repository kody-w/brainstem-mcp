"""brainstem-mcp as a plain MCP server for any host, no Claude plugin needed.

Every test starts a real stdio server process and speaks MCP to it, against a fake Brainstem on 127.0.0.1.
No real Brainstem is called and nothing signs in.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugins" / "brainstem"

# The three tools and their argument shapes, as shipped in 0.2.0. Hosts and users rely on them.
SHAPES = {
    "chat": {"properties": {"user_input": "string", "session_id": "string|null", "wait": "boolean"},
             "required": ["user_input"], "defaults": {"session_id": None, "wait": True}},
    "job_status": {"properties": {"session_id": "string"}, "required": ["session_id"], "defaults": {}},
    "capabilities": {"properties": {}, "required": [], "defaults": {}},
}


class FakeBrainstem(BaseHTTPRequestHandler):
    seen: list = []

    def _send(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"status": "ok", "version": "0.0.0-fake", "model": "fake", "agents": ["Echo"], "quarantined": []})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        FakeBrainstem.seen.append({"path": self.path, "body": body})
        self._send({"response": f"echo: {body['user_input']}", "model": "fake", "agent_logs": "[Echo]"})

    def log_message(self, *args):
        pass


@pytest.fixture
def brainstem():
    FakeBrainstem.seen = []
    server = HTTPServer(("127.0.0.1", 0), FakeBrainstem)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def commands():
    found = {
        "old entry point (python mcp_server.py)": [sys.executable, str(PLUGIN / "mcp_server.py")],
        "python -m brainstem_mcp": [sys.executable, "-m", "brainstem_mcp"],
    }
    if sys.prefix != sys.base_prefix:  # in a venv, it can stand in for the plugin's own built environment
        found["plugin launcher (launch.py, what Claude Code runs)"] = [sys.executable, str(PLUGIN / "launch.py")]
    script = shutil.which("brainstem-mcp")
    if script:
        found["brainstem-mcp console script"] = [script]
    elif os.getenv("CI"):
        found["brainstem-mcp console script"] = ["brainstem-mcp-is-not-installed"]
    return found


def env_for(url, tmp=None):
    env = dict(os.environ, BRAINSTEM_URL=url)
    if tmp is not None:
        (tmp / "venv").symlink_to(sys.prefix, target_is_directory=True)
        env["BRAINSTEM_MCP_HOME"] = str(tmp)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(PLUGIN), env.get("PYTHONPATH")]))
    return env


def talk(command, url, calls, tmp=None):
    """Initialize, list tools, then make each tools/call; return (tools, call results)."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env=env_for(url, tmp), cwd=str(REPO))
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "test", "version": "0"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    requests += [{"jsonrpc": "2.0", "id": 10 + n, "method": "tools/call", "params": {"name": name, "arguments": args}}
                 for n, (name, args) in enumerate(calls)]
    wanted = {1, 2, *range(10, 10 + len(calls))}
    replies = {}
    try:
        for request in requests:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            if "id" not in request:
                continue
            while request["id"] not in replies:
                line = process.stdout.readline()
                assert line, f"server exited early: {process.stderr.read()}"
                message = json.loads(line)
                if message.get("id") in wanted:
                    replies[message["id"]] = message
    finally:
        process.stdin.close()
        process.wait(15)
    assert replies[1]["result"]["serverInfo"]["name"] == "ai-brainstem"
    return replies[2]["result"]["tools"], [replies[10 + n] for n in range(len(calls))]


def shape(tool):
    schema = tool["inputSchema"]
    props = {}
    for key, spec in schema.get("properties", {}).items():
        props[key] = spec.get("type") or "|".join(option["type"] for option in spec["anyOf"])
    defaults = {key: spec["default"] for key, spec in schema.get("properties", {}).items() if "default" in spec}
    return {"properties": props, "required": schema.get("required", []), "defaults": defaults}


@pytest.mark.parametrize("label", list(commands()))
def test_every_start_command_serves_the_same_three_tools_and_answers(label, brainstem, tmp_path):
    tools, results = talk(commands()[label], brainstem, [("chat", {"user_input": "hello", "session_id": "h1"}),
                                                        ("capabilities", {})],
                          tmp_path if "launch.py" in label else None)
    assert [tool["name"] for tool in tools] == ["chat", "job_status", "capabilities"]
    assert {tool["name"]: shape(tool) for tool in tools} == SHAPES
    answer = json.loads(results[0]["result"]["content"][0]["text"])
    assert answer["response"] == "echo: hello" and answer["session_id"] == "h1"
    assert FakeBrainstem.seen[0] == {"path": "/chat", "body": {
        "user_input": "hello", "session_id": "h1", "conversation_history": []}}
    assert json.loads(results[1]["result"]["content"][0]["text"])["version"] == "0.0.0-fake"


def test_the_console_script_is_declared_and_points_at_the_cli():
    tomllib = pytest.importorskip("tomllib")
    project = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]
    assert project["scripts"] == {"brainstem-mcp": "brainstem_mcp.server:cli"}
    assert set(project["dependencies"]) == {"mcp>=2.0,<3", "requests>=2.31"}
    assert not any("rappmcp" in dependency for dependency in project["dependencies"])
    plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    sys.path.insert(0, str(PLUGIN))
    import brainstem_mcp
    assert project["version"] == plugin["version"] == brainstem_mcp.__version__


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "brainstem_mcp", *args], capture_output=True, text=True,
                          env=env_for("http://127.0.0.1:1"), stdin=subprocess.DEVNULL, timeout=30)


def test_help_and_version_answer_without_starting_a_server():
    assert run_cli("--help").stdout.startswith("usage: brainstem-mcp")
    assert run_cli("--version").stdout.strip().startswith("brainstem-mcp 0.")


def test_agents_flag_is_reserved_and_says_it_is_not_here_yet():
    done = run_cli("--agents")
    assert done.returncode == 2 and "not available yet" in done.stderr and done.stdout == ""


def test_the_base_tools_follow_the_naming_rule_agent_tools_will_share():
    sys.path.insert(0, str(PLUGIN))
    from brainstem_mcp import RESERVED_TOOL_NAMES, TOOL_NAME
    assert TOOL_NAME.pattern == r"^[a-zA-Z0-9_-]{1,64}$"
    assert set(SHAPES) | {"setup"} == RESERVED_TOOL_NAMES
    assert all(TOOL_NAME.match(name) for name in RESERVED_TOOL_NAMES)


def test_readme_has_one_line_installs_for_each_host_and_the_shared_story():
    readme = (REPO / "README.md").read_text()
    for line in ("claude mcp add brainstem -- ", "codex mcp add brainstem -- ", "Antigravity"):
        assert line in readme
    block = re.search(r"```json\n(\{.*?\})\n```", readme, re.S)
    servers = json.loads(block.group(1))["mcpServers"]["brainstem"]
    assert servers["command"] == "uvx" and servers["args"][-1] == "brainstem-mcp"
    assert "## brainstem-mcp or RAPPMCP?" in readme
    assert "\u2014" not in readme  # no em dashes
