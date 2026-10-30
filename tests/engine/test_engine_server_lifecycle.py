"""How the server runs calls: background helpers, cancel, disconnect, notifications, instructions,
shadowing helper files, unknown tools, and honest words when a call is stopped."""
import os
import shutil
import time

import anyio
import mcp_types as types
import pytest
from mcp.shared.exceptions import MCPError

from conftest import EXAMPLES, FIXTURES, connect, text_of, agent_tools
from brainstem_mcp import loader

ROUGH = FIXTURES / "rough"


def alive(pid):
    if os.name == "nt":
        return _alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill 0; ps tells the truth.
    state = os.popen(f"ps -o stat= -p {pid}").read().strip()
    return bool(state) and not state.startswith("Z")


def _alive_windows(pid):
    """On Windows os.kill(pid, 0) would stop the process, so ask for its exit code instead."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def wait_dead(pid, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return False


async def await_dead(pid, seconds=10):
    """Like wait_dead, but lets the client's event loop run (it sends the cancel)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not alive(pid):
            return True
        await anyio.sleep(0.1)
    return False


async def await_pid(path, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if path.exists() and path.read_text().strip():
            return int(path.read_text())
        await anyio.sleep(0.05)
    raise AssertionError("the agent never wrote its pid")


def read_pid(path, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if path.exists() and path.read_text().strip():
            return int(path.read_text())
        time.sleep(0.05)
    raise AssertionError("the agent never wrote its pid")


@pytest.mark.anyio
async def test_agent_that_starts_a_helper_answers_at_once(tmp_path):
    pid_file = tmp_path / "pid"
    async with connect(ROUGH, "--timeout", "5", env={"ROUGH_PID_FILE": str(pid_file)}) as (session, _):
        started = time.monotonic()
        result = await session.call_tool("Spawner", {})
        took = time.monotonic() - started
        child = read_pid(pid_file)
        running = alive(child)  # the worker is gone; the helper the agent started is the agent's business
    try:
        assert not result.is_error, text_of(result)
        assert text_of(result) == "started" and took < 4
        assert running
        if os.name != "nt":
            # On Windows the official MCP client ends its server's whole process tree when it closes.
            assert alive(child)
    finally:
        try:
            os.kill(child, 9)  # our own test process
        except (ProcessLookupError, PermissionError):  # Windows answers PermissionError once it is gone
            pass


def test_disconnect_stops_the_running_worker(tmp_path):
    """The client goes away mid-call (stdin closes). The worker must not be left running."""
    import json
    import subprocess
    import sys

    from conftest import ROOT

    pid_file = tmp_path / "pid"
    server = subprocess.Popen(
        [sys.executable, "-m", "brainstem_mcp", "--agents", str(ROUGH), "--timeout", "120"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT), "ROUGH_PID_FILE": str(pid_file),
             "BRAINSTEM_URL": "http://127.0.0.1:1"},
    )

    def send(message):
        server.stdin.write((json.dumps(message) + "\n").encode())
        server.stdin.flush()

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
        assert b'"id":1' in server.stdout.readline()
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "Sleepy", "arguments": {}}})
        worker = read_pid(pid_file)
        assert alive(worker)
        server.stdin.close()
        assert wait_dead(worker), "the worker outlived its client"
    finally:
        if server.poll() is None:
            server.kill()


@pytest.mark.anyio
async def test_cancel_stops_the_running_worker(tmp_path):
    pid_file = tmp_path / "pid"
    async with connect(ROUGH, "--timeout", "120", env={"ROUGH_PID_FILE": str(pid_file)}) as (session, _):
        with anyio.move_on_after(2):
            await session.call_tool("Sleepy", {})
        worker = await await_pid(pid_file)
        assert await await_dead(worker), "a cancelled call left its worker running"
        assert text_of(await session.call_tool("Fine", {})) == "still here"


def test_worker_dies_when_its_server_is_killed(tmp_path):
    """Belt and braces: a worker whose parent vanished stops itself."""
    import subprocess
    import sys

    from conftest import ROOT

    pid_file = tmp_path / "pid"
    parent = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess,sys,time;"
         "p=subprocess.Popen([sys.executable,'-m','brainstem_mcp.runner','call',sys.argv[1],'Sleepy'],"
         "stdin=subprocess.PIPE,start_new_session=(sys.platform!='win32'));"
         "p.stdin.write(b'{}');p.stdin.close();time.sleep(60)",
         str(ROUGH / "rough_agent.py")],
        env={**os.environ, "PYTHONPATH": str(ROOT), "ROUGH_PID_FILE": str(pid_file)},
    )
    try:
        worker = read_pid(pid_file)
        parent.kill()
        parent.wait()
        assert wait_dead(worker), "the worker kept running after its server was killed"
    finally:
        if parent.poll() is None:
            parent.kill()


@pytest.mark.anyio
async def test_one_drop_sends_one_notification(tmp_path):
    shutil.copy(EXAMPLES / "echo_agent.py", tmp_path / "echo_agent.py")
    seen = []

    async def on_message(message):
        if isinstance(message, types.ToolListChangedNotification):
            seen.append(message)

    async with connect(tmp_path, "--poll", "0.2", on_message=on_message) as (session, _):
        for _ in range(3):
            await session.list_tools()
        await session.call_tool("Echo", {"text": "x"})
        shutil.copy(EXAMPLES / "notes_agent.py", tmp_path / "notes_agent.py")
        with anyio.fail_after(15):
            while not seen:
                await anyio.sleep(0.05)
        await anyio.sleep(1.5)
    assert len(seen) == 1


@pytest.mark.anyio
async def test_instructions_follow_a_rescan(tmp_path):
    """A dropped agent's system_context reaches initialize and server/discover after the rescan."""
    from brainstem_mcp.engine import Engine
    from mcp.server.lowlevel import NotificationOptions

    shutil.copy(FIXTURES / "names" / "greeter_agent.py", tmp_path / "greeter_agent.py")
    app = Engine(tmp_path)
    await app.scan()
    server = app.build()
    app.options = server.create_initialization_options(NotificationOptions(tools_changed=True))
    assert not server.instructions
    shutil.copy(EXAMPLES / "echo_agent.py", tmp_path / "echo_agent.py")
    await app.rescan()
    assert "Echo says text back" in (server.instructions or "")
    assert "Echo says text back" in (app.options.instructions or "")
    assert "Echo says text back" in (server.create_initialization_options().instructions or "")


@pytest.mark.anyio
async def test_a_helper_file_cannot_shadow_the_worker():
    assert set(loader.load(FIXTURES / "shadow")[0]) == {"Echo"}
    async with connect(FIXTURES / "shadow") as (session, _):
        names = [t.name for t in agent_tools(await session.list_tools())]
        assert names == ["Echo"]
        assert text_of(await session.call_tool("Echo", {"text": "hi"})) == "hi"


@pytest.mark.anyio
async def test_unknown_tool_is_a_jsonrpc_invalid_params_error():
    async with connect(EXAMPLES) as (session, _):
        with pytest.raises(MCPError) as caught:
            await session.call_tool("Nope", {})
    assert caught.value.code == -32602 and "Nope" in caught.value.message


def test_constructor_error_stops_the_rest_of_that_file():
    agents, _, _, errors = loader.load(FIXTURES / "ctor")
    assert set(agents) == {"Aok"}
    assert len(errors) == 1 and "Boom()" in errors[0]["error"]


@pytest.mark.anyio
async def test_stopped_call_never_claims_nothing_happened():
    async with connect(ROUGH, "--timeout", "2") as (session, _):
        hang = text_of(await session.call_tool("Hang", {}))
        crash = text_of(await session.call_tool("Crash", {}))
    for text in (hang, crash):
        assert "Nothing else was affected" not in text
        assert "may have done part of its work" in text and "Check before" in text
