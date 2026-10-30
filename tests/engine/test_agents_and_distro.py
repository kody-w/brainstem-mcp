"""--agents and --distro: the Brainstem's own three tools, plus one tool per agent file.

A distro is the unchanged kernel, pinned, plus agent files pinned by sha256. A file whose bytes do
not match its pin is never run. No real Brainstem is called: BRAINSTEM_URL is a fake on 127.0.0.1.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import BASE_TOOLS, EXAMPLES, REPO, ROOT, agent_tools, connect, server_env, text_of
from brainstem_mcp import distro, kernel

PACKAGED_PIN = json.loads((ROOT / "brainstem_mcp" / "kernel.json").read_text())

CHAT_AGENT = '''
from agents.basic_agent import BasicAgent


class Chat(BasicAgent):
    def __init__(self):
        super().__init__(name="chat", metadata={"name": "chat", "description": "Takes a reserved name."})

    def perform(self, **kwargs):
        return "never served"
'''

STORAGE = '''
class AzureFileStorageManager:
    def read_file(self, *args):
        return "from the Brainstem's local storage"
'''

MEMORY_AGENT = '''
from agents.basic_agent import BasicAgent
from utils.azure_file_storage import AzureFileStorageManager


class Memory(BasicAgent):
    def __init__(self):
        super().__init__(name="Memory", metadata={"name": "Memory", "description": "Reads storage."})

    def perform(self, **kwargs):
        return AzureFileStorageManager().read_file()
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_distro(folder, files, pins=None):
    """Copy the files into folder/agents and write folder/distro.json pinning each one."""
    (folder / "agents").mkdir(parents=True, exist_ok=True)
    agents = []
    for source in files:
        target = folder / "agents" / source.name
        shutil.copy(source, target)
        agents.append({"path": f"agents/{source.name}", "sha256": (pins or {}).get(source.name, sha(target))})
    manifest = folder / "distro.json"
    manifest.write_text(json.dumps({"schema": distro.SCHEMA, "name": "test-distro",
                                    "kernel": PACKAGED_PIN, "agents": agents}, indent=2))
    return manifest


@pytest.fixture
def brainstem():
    seen = []

    class Handler(BaseHTTPRequestHandler):
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
            seen.append(body)
            self._send({"response": f"echo: {body['user_input']}", "model": "fake"})

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", seen
    server.shutdown()


@pytest.mark.anyio
async def test_agents_sit_beside_chat_job_status_and_capabilities(brainstem):
    url, seen = brainstem
    async with connect(EXAMPLES, env={"BRAINSTEM_URL": url}) as (session, init):
        assert init.server_info.name == "ai-brainstem"
        assert "A local AI Brainstem" in init.instructions and "Echo says text back" in init.instructions
        listed = await session.list_tools()
        assert [t.name for t in listed.tools] == [*BASE_TOOLS, "Echo", "Notes"]
        chat = await session.call_tool("chat", {"user_input": "hi", "session_id": "s1"})
        echo = await session.call_tool("Echo", {"text": "hi", "shout": True})
    assert json.loads(text_of(chat))["response"] == "echo: hi" and not chat.is_error
    assert seen == [{"user_input": "hi", "session_id": "s1", "conversation_history": []}]
    assert text_of(echo) == "HI" and not echo.is_error


@pytest.mark.anyio
async def test_chat_still_fails_in_plain_words_when_the_brainstem_is_down():
    async with connect(EXAMPLES) as (session, _):
        result = await session.call_tool("capabilities", {})
        echo = await session.call_tool("Echo", {"text": "agents still work"})
    assert result.is_error and "not running at http://127.0.0.1:1" in text_of(result)
    assert text_of(echo) == "agents still work"


def test_an_agent_cannot_take_a_reserved_name(tmp_path):
    from brainstem_mcp import loader

    shutil.copy(EXAMPLES / "echo_agent.py", tmp_path / "echo_agent.py")
    (tmp_path / "chat_agent.py").write_text(CHAT_AGENT)
    agents, _, quarantined, _ = loader.load(tmp_path)
    assert set(agents) == {"Echo"}
    assert any(q["class"] == "Chat" and "belongs to the Brainstem" in q["reason"] for q in quarantined)


@pytest.mark.anyio
async def test_agents_with_no_folder_serves_the_brainstems_own_agents_folder(tmp_path):
    """The default is ~/.brainstem/src/rapp_brainstem/agents. This one is a stand-in under a fake home."""
    home = tmp_path / "home"
    brainstem_dir = home / ".brainstem" / "src" / "rapp_brainstem"
    (brainstem_dir / "agents").mkdir(parents=True)
    shutil.copy(EXAMPLES / "echo_agent.py", brainstem_dir / "agents" / "echo_agent.py")
    (brainstem_dir / "local_storage.py").write_text(STORAGE)
    (brainstem_dir / "agents" / "memory_agent.py").write_text(MEMORY_AGENT)
    before = sorted(p.relative_to(brainstem_dir) for p in brainstem_dir.rglob("*"))
    env = {"HOME": str(home), "USERPROFILE": str(home), "BRAINSTEM_MCP_CACHE": str(tmp_path / "cache")}
    import os
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "brainstem_mcp", "--agents"],
                                   env=server_env(env), cwd=str(REPO))
    with open(os.devnull, "w") as quiet:
        async with stdio_client(params, errlog=quiet) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = [t.name for t in agent_tools(await session.list_tools())]
                memory = await session.call_tool("Memory", {})
    assert names == ["Echo", "Memory"]
    assert text_of(memory) == "from the Brainstem's local storage"  # the Brainstem's utils shims, same as inside it
    assert sorted(p.relative_to(brainstem_dir) for p in brainstem_dir.rglob("*")) == before  # nothing written


@pytest.mark.anyio
async def test_a_distro_serves_only_files_whose_bytes_match_their_pins(tmp_path):
    manifest = make_distro(tmp_path, [EXAMPLES / "echo_agent.py", EXAMPLES / "notes_agent.py"],
                           pins={"notes_agent.py": "0" * 64})
    log = tmp_path / "log.txt"
    with open(log, "w") as errlog:
        async with connect(None, distro=manifest, errlog=errlog) as (session, init):
            names = [t.name for t in agent_tools(await session.list_tools())]
            echo = await session.call_tool("Echo", {"text": "pinned"})
    assert names == ["Echo"]
    assert text_of(echo) == "pinned"
    assert "refused notes_agent.py: its sha256 is" in log.read_text()


@pytest.mark.anyio
async def test_a_file_changed_after_its_check_is_refused_at_call_time(tmp_path):
    manifest = make_distro(tmp_path, [EXAMPLES / "echo_agent.py"])
    target = tmp_path / "agents" / "echo_agent.py"
    async with connect(None, "--poll", "600", distro=manifest) as (session, _):
        assert [t.name for t in agent_tools(await session.list_tools())] == ["Echo"]
        target.write_text(target.read_text().replace("else text", "else 'swapped: ' + text"))
        result = await session.call_tool("Echo", {"text": "hi"})
    assert result.is_error and "refused echo_agent.py" in text_of(result)


@pytest.mark.anyio
async def test_a_distro_and_a_folder_together(tmp_path):
    manifest = make_distro(tmp_path / "d", [EXAMPLES / "notes_agent.py"])
    shutil.copy(EXAMPLES / "echo_agent.py", tmp_path / "echo_agent.py")
    async with connect(tmp_path, distro=manifest) as (session, _):
        names = [t.name for t in agent_tools(await session.list_tools())]
    assert names == ["Notes", "Echo"]  # distro files first, then the folder


def test_a_distro_brings_its_own_kernel_pin(tmp_path):
    manifest = make_distro(tmp_path, [EXAMPLES / "echo_agent.py"])
    data = json.loads(manifest.read_text())
    data["kernel"] = dict(data["kernel"], commit="1" * 40)
    manifest.write_text(json.dumps(data))
    seen = subprocess.run([sys.executable, "-c", "from brainstem_mcp import kernel; print(kernel.PIN['commit'])"],
                          env=server_env({"BRAINSTEM_MCP_KERNEL_PIN": str(manifest)}), cwd=str(REPO),
                          capture_output=True, text=True, check=True).stdout.strip()
    assert seen == "1" * 40
    assert kernel.read_pin(manifest)["commit"] == "1" * 40


@pytest.mark.parametrize("change, says", [
    (lambda d: d.update(schema="other/1"), "brainstem-distro/1"),
    (lambda d: d.update(kernel={"repository": "https://github.com/x/y"}), "commit"),
    (lambda d: d["agents"].append({"path": "../outside_agent.py", "sha256": "0" * 64}), "inside the manifest's folder"),
    (lambda d: d["agents"].append({"path": "agents/x_agent.py"}), "needs a sha256"),
])
def test_a_broken_manifest_stops_the_start_with_a_plain_reason(tmp_path, change, says):
    manifest = make_distro(tmp_path, [EXAMPLES / "echo_agent.py"])
    data = json.loads(manifest.read_text())
    change(data)
    manifest.write_text(json.dumps(data))
    done = subprocess.run([sys.executable, "-m", "brainstem_mcp", "--distro", str(manifest)], env=server_env(),
                          cwd=str(REPO), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    assert done.returncode == 2 and says in done.stderr and done.stdout == ""


def test_distro_check_and_pin(tmp_path):
    manifest = make_distro(tmp_path, [EXAMPLES / "echo_agent.py"], pins={"echo_agent.py": "0" * 64})
    run = lambda *a: subprocess.run([sys.executable, "-m", "brainstem_mcp.distro", *a, str(manifest)],
                                    env=server_env(), cwd=str(REPO), capture_output=True, text=True)
    before = run("check")
    assert before.returncode == 1 and "FAIL agents/echo_agent.py" in before.stdout
    assert "pinned agents/echo_agent.py" in run("pin").stdout
    after = run("check")
    assert after.returncode == 0 and "ok   agents/echo_agent.py: matches" in after.stdout


def test_agents_over_http_is_refused_in_plain_words():
    done = subprocess.run([sys.executable, "-m", "brainstem_mcp", "--agents", str(EXAMPLES), "--http", "7099"],
                          env=server_env(), cwd=str(REPO), capture_output=True, text=True, timeout=60)
    assert done.returncode == 2 and "stdio only" in done.stderr


def test_a_missing_agents_folder_stops_the_start_with_a_plain_reason(tmp_path):
    done = subprocess.run([sys.executable, "-m", "brainstem_mcp", "--agents", str(tmp_path / "nope")],
                          env=server_env(), cwd=str(REPO), capture_output=True, text=True, timeout=60,
                          stdin=subprocess.DEVNULL)
    assert done.returncode == 2 and "does not exist" in done.stderr and done.stdout == ""


@pytest.mark.anyio
async def test_a_distro_can_list_its_base_tools_after_the_agents(tmp_path):
    """A distro alias (RAPPMCP) keeps its old order: agent tools first, its own extra tool last."""
    from brainstem_mcp.engine import BaseTools, Engine
    import mcp_types as types

    class Extra(BaseTools):
        names = frozenset({"extra"})

        async def tools(self):
            return [types.Tool(name="extra", description="x", input_schema={"type": "object"})]

    shutil.copy(EXAMPLES / "echo_agent.py", tmp_path / "echo_agent.py")
    for first, order in ((True, ["extra", "Echo"]), (False, ["Echo", "extra"])):
        engine = Engine(tmp_path, base=Extra(), base_first=first)
        await engine.scan()
        assert [t.name for t in await engine.listed()] == order
