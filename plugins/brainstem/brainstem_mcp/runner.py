"""The short-lived worker. The engine starts one per scanned file and one per tools/call.

    python -m brainstem_mcp.runner list <file-or-folder> [--sha256 HEX]
    python -m brainstem_mcp.runner call <agent-file> <tool-name> [--sha256 HEX]   (arguments as JSON on stdin)

With --sha256 the file is refused unless its bytes match (a distro manifest pins every file).

It prints one JSON object on stdout. Anything the agent prints goes to stderr,
so it can never corrupt the answer.
"""
import json
import os
import signal
import sys
import threading
import time

# Never write __pycache__ anywhere: not into the kernel install, not into the agents' folder.
sys.dont_write_bytecode = True


def _watch_parent():
    """Stop this worker, and whatever its agent started, if the server that started it goes away.

    The server stops its workers on a timeout, a cancel or a disconnect. This covers the rest:
    a server killed outright. The worker runs in its own session, so this kills only that.
    """
    parent = os.getppid()

    def watch():
        while True:
            time.sleep(0.5)
            if os.getppid() != parent:
                try:
                    os.killpg(os.getpgrp(), signal.SIGKILL)
                finally:
                    os._exit(1)

    threading.Thread(target=watch, name="brainstem-mcp-parent-watch", daemon=True).start()


SERVER_PID_ENV = "BRAINSTEM_MCP_SERVER_PID"


class _WindowsTree:
    """Windows has no sessions. A Job Object plays the part: this worker puts itself in a job
    that kills every process in it when the worker dies, so a stopped or orphaned worker takes
    whatever its agent started with it. A worker that answers lets go of the job first, so a
    helper the agent started on purpose keeps running, as it does on Linux and macOS.
    """

    KILL_ON_JOB_CLOSE = 0x2000
    EXTENDED_LIMITS = 9
    SYNCHRONIZE = 0x00100000

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes, self.wintypes = ctypes, wintypes
        self.k32 = k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                        "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self.Extended = Extended
        self.job = k32.CreateJobObjectW(None, None)
        if not self.job:
            raise OSError(ctypes.get_last_error(), "CreateJobObject failed")
        self._limit(self.KILL_ON_JOB_CLOSE)
        if not k32.AssignProcessToJobObject(self.job, k32.GetCurrentProcess()):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def _limit(self, flags):
        info = self.Extended()
        info.BasicLimitInformation.LimitFlags = flags
        if not self.k32.SetInformationJobObject(self.job, self.EXTENDED_LIMITS, self.ctypes.byref(info),
                                                self.ctypes.sizeof(info)):
            raise OSError(self.ctypes.get_last_error(), "SetInformationJobObject failed")

    def release(self):
        """The worker answered: let what its agent started keep running."""
        try:
            self._limit(0)
        except OSError:
            pass

    def stop_tree(self):
        """Stop this worker and every process its agent started, then exit."""
        try:
            import subprocess

            subprocess.run(["taskkill", "/F", "/T", "/PID", str(os.getpid())],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        finally:
            os._exit(1)  # the job closes with this process and kills whatever is left in it

    def watch_parent(self):
        """Stop the tree when the server that started this worker goes away."""
        parent = int(os.environ.get(SERVER_PID_ENV) or os.getppid())
        handle = self.k32.OpenProcess(self.SYNCHRONIZE, False, parent)
        if not handle:
            self.stop_tree()  # the server is already gone

        def watch():
            self.k32.WaitForSingleObject(handle, 0xFFFFFFFF)
            self.stop_tree()

        threading.Thread(target=watch, name="brainstem-mcp-parent-watch", daemon=True).start()


def _isolate():
    """Put this worker in charge of everything its agent starts. Returns the Windows job, or None."""
    if os.name != "nt":
        if os.getpgrp() == os.getpid():  # only in its own session; never kill a group it does not lead
            _watch_parent()
        return None
    try:
        tree = _WindowsTree()
    except OSError as error:
        print(f"[brainstem-mcp] worker isolation unavailable: {error}", file=sys.stderr)
        return None
    tree.watch_parent()
    return tree


def _answer_channel():
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return out


def _list(target, sha256=None):
    """Scan one file or folder. The server scans one file per worker, so a file that
    exits or hangs at import costs only its own tools."""
    from . import loader

    if sha256 is None:
        agents, where, quarantined, errors = loader.load(target)
    else:
        quarantined, errors = [], []
        agents = loader.load_file(target, quarantined, errors, sha256=sha256)
        where = {name: str(target) for name in agents}
    tools = []
    for name, instance in agents.items():
        tool = loader.tool_of(instance)
        tool["file"] = where[name]
        tool["class"] = type(instance).__name__
        try:
            context = instance.system_context()
        except Exception as error:
            errors.append({"file": os.path.basename(where[name]), "error": f"system_context: {error}"})
            context = None
        if isinstance(context, str) and context.strip():
            tool["context"] = context.strip()
        tools.append(tool)
    return {"tools": tools, "quarantined": quarantined, "errors": errors}


def _structured(result):
    """The JSON object an agent answered with, or None. The text answer stays str(result)."""
    if isinstance(result, dict):
        value = result
    else:
        try:
            value = json.loads(str(result))
        except ValueError:
            return None
    if not isinstance(value, dict):
        return None
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return None


def _call(path, name, args, sha256=None):
    from . import loader

    quarantined, errors = [], []
    agents = loader.load_file(path, quarantined, errors, sha256=sha256)
    agent = agents.get(name)
    if agent is None:
        reason = errors[0]["error"] if errors else f"No agent named {name!r} in {os.path.basename(path)}."
        return {"ok": False, "text": reason}
    if not isinstance(args, dict):
        return {"ok": False, "text": "Error: Tool arguments must be a valid JSON object."}
    try:
        result = agent.perform(**args)
    except Exception as error:
        return {"ok": False, "text": f"Error: {error}"}
    reply = {"ok": True, "text": str(result)}
    structured = _structured(result)
    if structured is not None:
        reply["structured"] = structured
    return reply


def main(argv):
    tree = _isolate()
    out = _answer_channel()
    sha256 = None
    if len(argv) >= 2 and argv[-2] == "--sha256":
        sha256, argv = argv[-1], argv[:-2]
    try:
        if len(argv) == 2 and argv[0] == "list":
            reply = _list(argv[1], sha256)
        elif len(argv) == 3 and argv[0] == "call":
            raw = sys.stdin.read()
            reply = _call(argv[1], argv[2], json.loads(raw) if raw.strip() else {}, sha256)
        else:
            reply = {"ok": False, "text": "usage: runner list <path> [--sha256 HEX] | runner call <file> <tool> [--sha256 HEX]"}
    except Exception as error:
        reply = {"ok": False, "text": f"Error: {type(error).__name__}: {error}"}
    out.write(json.dumps(reply))
    out.flush()
    if tree is not None:
        tree.release()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
