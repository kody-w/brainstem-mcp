"""The engine: serve agent.py files as MCP tools, one short-lived worker per file scan and per call.

The engine never imports agent code. Workers (brainstem_mcp.runner) scan each file alone and run
each call alone, each with a timeout, so a crashing or hanging agent costs one worker, never the
server. On Linux and macOS a worker gets its own session; on Windows it puts itself in a Job
Object. Either way a stopped worker takes whatever its agent started with it.

What it serves:
    a folder      every *_agent.py in it, sorted by name, the way the Brainstem loads agents/
    a file        that one agent file
    a distro      a manifest (kernel pin + agent files pinned by sha256); a file whose bytes do
                  not match its pin is refused, and the kernel pin is the manifest's own

Next to the agent tools it can serve base tools (brainstem-mcp's chat, job_status, capabilities).
Their names are reserved: no agent tool takes them.
"""
import asyncio
import json
import os
import pathlib
import signal
import sys
import tempfile

import anyio
import mcp_types as types
from mcp_types.jsonrpc import INVALID_PARAMS
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler, ToolsListChanged

from . import distro as distros
from . import kernel, loader

SCAN_TIMEOUT = 10.0  # seconds one file may take to import; well under a client's startup wait
STARTUP_WAIT = 2.0  # how long initialize waits for the first scan, so instructions can ride along
SCAN_WORKERS = max(1, min(8, os.cpu_count() or 1))
WINDOWS = os.name == "nt"

# The engine only reads the kernel install. Neither the server nor any worker writes bytecode.
sys.dont_write_bytecode = True

PACKAGE_HOME = str(pathlib.Path(__file__).resolve().parents[1])
# Agents read their time budget from these (RAPPMCP_CALL_TIMEOUT is the older name, kept working).
TIMEOUT_ENVS = ("BRAINSTEM_MCP_CALL_TIMEOUT", "RAPPMCP_CALL_TIMEOUT")
SERVER_PID_ENV = "BRAINSTEM_MCP_SERVER_PID"

LOG_PREFIX = "brainstem-mcp"


def _log(message):
    print(f"[{LOG_PREFIX}] {message}", file=sys.stderr, flush=True)


def fingerprint(paths):
    """What changed on disk: for each path, its .py files (a folder) or itself (name, size, mtime)."""
    marks = []
    for target in paths:
        target = pathlib.Path(target)
        files = sorted(target.glob("*.py")) if target.is_dir() else [target]
        for path in files:
            try:
                stat = path.stat()
            except FileNotFoundError:
                marks.append((str(path), None, None))
                continue
            marks.append((str(path), stat.st_size, stat.st_mtime_ns))
    return tuple(marks)


def _stop(proc):
    """Stop a worker and everything it started in its own session. Never touches other processes."""
    if proc.returncode is None:
        if WINDOWS:
            _stop_windows(proc)
            return
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _stop_windows(proc):
    """Windows: stop the worker's process tree. The worker's own job kills anything left over."""
    import subprocess

    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    try:
        proc.kill()
    except ProcessLookupError:
        pass


def _isolation():
    """POSIX: a new session per worker. Windows: a new process group (the worker adds a job)."""
    if WINDOWS:
        import subprocess

        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


async def run_worker(args, stdin_text, timeout, cwd=None, module="brainstem_mcp.runner"):
    """Run `python -m brainstem_mcp.runner <args>` and return (reply dict or None, note).

    The call is over when the worker exits, not when its pipes close: a helper the agent
    started may keep stderr open, and that is the agent's business. The worker gets its own
    session, so a timeout, a cancel or a disconnect stops it and whatever it started then.
    It runs from the engine's own folder, so a file in the served folder (a json.py) can never
    replace a module the engine itself imports.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [PACKAGE_HOME, env.get("PYTHONPATH")]))
    for name in TIMEOUT_ENVS:
        env[name] = f"{timeout:g}"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if WINDOWS:  # the worker watches this server, even through a venv launcher
        env[SERVER_PID_ENV] = str(os.getpid())
    with tempfile.TemporaryFile() as errors:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", module, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=errors,
            cwd=cwd or PACKAGE_HOME,
            env=env,
            **_isolation(),
        )
        out = b""

        async def finish():
            nonlocal out
            try:
                proc.stdin.write(stdin_text.encode())
                await proc.stdin.drain()
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                pass
            out = await proc.stdout.read()  # the answer channel is the worker's alone
            await proc.wait()

        try:
            await asyncio.wait_for(finish(), timeout)
        except asyncio.TimeoutError:
            _stop(proc)
            await proc.wait()
            return None, f"did not finish within {timeout:g} seconds, so it was stopped"
        finally:
            _stop(proc)  # a cancel or a disconnect lands here too: stop it before going away
        errors.seek(0)
        err = errors.read()
    if err:
        _log(err.decode(errors="replace").strip()[-2000:])
    try:
        return json.loads(out.decode()), ""
    except ValueError:
        return None, f"stopped without an answer (exit code {proc.returncode})"


def _tool(t):
    hints = t.get("annotations")
    return types.Tool(
        name=t["name"],
        description=t["description"],
        input_schema=t["inputSchema"],
        annotations=types.ToolAnnotations(
            read_only_hint=hints.get("readOnlyHint"), destructive_hint=hints.get("destructiveHint")
        ) if hints else None,
    )


class BaseTools:
    """Tools served beside the agents, by name. Subclass and fill in."""

    names = frozenset()
    instructions = None

    async def tools(self):
        return []

    async def call(self, name, arguments):
        """Return a CallToolResult."""
        raise NotImplementedError


class Engine:
    """Serve a folder, a file, a distro manifest, or a distro plus a folder."""

    def __init__(self, target=None, timeout=120.0, poll=1.0, scan_timeout=SCAN_TIMEOUT, distro=None,
                 base=None, name="brainstem-mcp", version="", stopped_by="brainstem-mcp"):
        self.target = pathlib.Path(target).expanduser().resolve() if target is not None else None
        if self.target is not None and not self.target.exists():
            raise FileNotFoundError(f"{self.target} does not exist")
        self.distro = pathlib.Path(distro).expanduser().resolve() if distro is not None else None
        if self.distro is not None:
            distros.read(self.distro)  # a broken manifest stops the start, with a plain reason
            kernel.use_pin(self.distro)
        if self.target is None and self.distro is None:
            raise ValueError("nothing to serve: name a folder, a file or a distro manifest")
        self.folder = None if self.target is None else (self.target if self.target.is_dir() else self.target.parent)
        self.timeout = timeout
        self.poll = poll
        self.scan_timeout = scan_timeout
        self.base = base or BaseTools()
        self.name = name
        self.version = version
        self.stopped_by = stopped_by
        self.ready = asyncio.Event()  # set once the first scan is done
        self.tools = []
        self.report = {}
        self.mark = None
        self.connections = []
        self.server = None
        self.options = None
        self.bus = InMemorySubscriptionBus()

    def watched(self):
        paths = []
        if self.distro is not None:
            paths.append(self.distro)
            try:
                paths.extend(path for path, _ in distros.read(self.distro)["agents"])
            except distros.DistroError:
                pass
        if self.target is not None:
            paths.append(self.target)
        return paths

    def entries(self, errors):
        """(path, sha256 or None) for every file to scan, distro files first."""
        found = []
        if self.distro is not None:
            try:
                manifest = distros.read(self.distro)
            except distros.DistroError as error:
                errors.append({"file": self.distro.name, "error": str(error)})
                manifest = {"agents": [], "missing": []}
            found.extend(manifest["agents"])
            for path in manifest["missing"]:
                errors.append({"file": pathlib.Path(path).name, "error": f"refused {path}: the manifest lists it, "
                                                                          "but the file is not there"})
        if self.target is not None:
            try:
                found.extend((path, None) for path in loader.agent_files(self.target))
            except FileNotFoundError as error:
                _log(str(error))
        return found

    async def scan_file(self, path, sha256, limit):
        """Scan one agent file in its own worker. A file that exits or hangs loses only its own tools."""
        path = pathlib.Path(path)
        args = ["list", str(path)] + (["--sha256", sha256] if sha256 else [])
        async with limit:
            reply, note = await run_worker(args, "", self.scan_timeout)
        if reply is not None and "tools" in reply:
            for tool in reply["tools"]:
                if sha256:
                    tool["sha256"] = sha256
            return reply
        if note.startswith("did not finish"):
            reason = f"skipped {path.name}: it did not finish loading within {self.scan_timeout:g} seconds (--scan-timeout)"
        else:
            reason = f"skipped {path.name}: {note or (reply or {}).get('text', 'scan failed')}"
        return {"tools": [], "quarantined": [], "errors": [{"file": path.name, "error": reason}]}

    async def scan(self):
        self.mark = fingerprint(self.watched())
        early = []
        files = self.entries(early)
        limit = asyncio.Semaphore(SCAN_WORKERS)
        scans = await asyncio.gather(*(self.scan_file(path, sha, limit) for path, sha in files))
        reply = loader.merge([{"tools": [], "quarantined": [], "errors": early}] + list(scans))
        self.tools = reply["tools"]
        self.report = reply
        for item in reply["quarantined"]:
            _log(f"quarantined {item['class']} in {item['file']}: {item['reason']}")
        for item in reply["errors"]:
            text = item["error"]
            _log(text if text.startswith(("skipped ", "refused ")) else f"could not load {item['file']}: {text}")
        where = " and ".join(str(p) for p in (self.distro, self.target) if p is not None)
        _log(f"{len(self.tools)} tool(s) from {where}")
        if self.server is not None:
            self.server.instructions = self.instructions()
        if self.options is not None:
            self.options.instructions = self.instructions()
        return self.tools

    async def first_scan(self):
        try:
            await self.scan()
        finally:
            self.ready.set()

    def instructions(self):
        parts = ([self.base.instructions] if self.base.instructions else []) + self.report.get("instructions", [])
        return "\n\n".join(parts) or None

    async def listed(self):
        return list(await self.base.tools()) + [_tool(t) for t in self.tools]

    async def call(self, name, arguments):
        """Returns (failed, text, structured) for an agent tool."""
        await self.ready.wait()
        tool = next((t for t in self.tools if t["name"] == name), None)
        if tool is None:
            # MCP: an unknown tool is a protocol error (-32602), not a tool result.
            raise MCPError(INVALID_PARAMS, f"Unknown tool: {name}")
        args = ["call", tool["file"], name] + (["--sha256", tool["sha256"]] if tool.get("sha256") else [])
        reply, note = await run_worker(args, json.dumps(arguments or {}), self.timeout)
        if reply is None:
            note = note.replace("so it was stopped", f"so {self.stopped_by} stopped it")
            return True, (f"The agent {name} {note}. It may have done part of its work before it stopped "
                          "(a message may already be sent). Check before you try again, and do not repeat "
                          "an action that changes something."), None
        return (not reply.get("ok")), reply.get("text", ""), reply.get("structured")

    def remember(self, ctx):
        """Keep one entry per client connection. Request contexts are new every time; the
        connection behind them is not."""
        session = getattr(ctx, "session", None)
        connection = getattr(session, "_connection", None) or session
        if connection is not None and all(c is not connection for c in self.connections):
            self.connections.append(connection)

    async def rescan(self):
        await self.scan()
        await self.bus.publish(ToolsListChanged())
        for connection in list(self.connections):
            try:
                await connection.send_tool_list_changed()
            except Exception as error:
                _log(f"could not send tools/list_changed: {error}")
                self.connections.remove(connection)

    async def watch(self):
        await self.ready.wait()
        while True:
            await anyio.sleep(self.poll)
            if fingerprint(self.watched()) != self.mark:
                await self.rescan()

    def build(self):
        async def on_list_tools(ctx, params):
            self.remember(ctx)
            await self.ready.wait()  # the first scan; each file has --scan-timeout at most
            return types.ListToolsResult(tools=await self.listed())

        async def on_call_tool(ctx, params):
            self.remember(ctx)
            if params.name in self.base.names:
                return await self.base.call(params.name, params.arguments or {})
            failed, text, structured = await self.call(params.name, params.arguments)
            return types.CallToolResult(
                content=[types.TextContent(text=text)], structured_content=structured, is_error=failed
            )

        async def on_initialized(ctx, params):
            self.remember(ctx)

        self.server = Server(
            self.name,
            version=self.version,
            instructions=self.instructions(),
            on_list_tools=on_list_tools,
            on_call_tool=on_call_tool,
            on_subscriptions_listen=ListenHandler(self.bus),
        )
        self.server.add_notification_handler("notifications/initialized", types.NotificationParams, on_initialized)
        return self.server


def _warm_kernel():
    """Find (or fetch once) the pinned kernel file before any worker needs it."""
    try:
        return kernel.locate()
    except Exception as error:
        _log(f"kernel: {error}")
        return None


async def serve_stdio(engine):
    """Answer initialize at once, serve the healthy tools, and skip a file that hangs.

    The first scan runs in the background. initialize waits for it at most STARTUP_WAIT
    seconds (so instructions usually ride along); tools/list waits for it to finish,
    which is at most --scan-timeout plus a moment.
    """
    server = engine.build()
    options = engine.options = server.create_initialization_options(NotificationOptions(tools_changed=True))
    async with stdio_server() as (read, write):
        async with anyio.create_task_group() as group:

            async def startup():
                await asyncio.to_thread(_warm_kernel)
                await engine.first_scan()

            group.start_soon(startup)
            with anyio.move_on_after(min(STARTUP_WAIT, engine.scan_timeout)):
                await engine.ready.wait()
            group.start_soon(engine.watch)
            await server.run(read, write, options)
            group.cancel_scope.cancel()
