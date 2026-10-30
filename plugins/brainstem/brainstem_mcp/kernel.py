"""Find the grail's basic_agent.py, prove it is the pinned one, and expose it.

brainstem-mcp never edits, forks or patches the kernel. It reads one file,
agents/basic_agent.py, at the commit pinned in kernel.json (or in a distro
manifest), and checks its sha256 before using it. Agents import it the same
ways they do inside a Brainstem: `from agents.basic_agent import BasicAgent`
or `from openrappter.agents.basic_agent import BasicAgent`.

Settings (each older RAPPMCP name still works, as an alias):
    BRAINSTEM_MCP_KERNEL      use this basic_agent.py (RAPPMCP_KERNEL)
    BRAINSTEM_MCP_CACHE       where a fetched kernel file is kept (RAPPMCP_CACHE)
    BRAINSTEM_MCP_KERNEL_PIN  a JSON file holding the pin to use: a kernel pin, or a
                              distro manifest whose "kernel" is one (the server sets it
                              for its workers when it serves a distro)
"""
import hashlib
import json
import os
import pathlib
import sys
import types
import urllib.request

PACKAGED_PIN = pathlib.Path(__file__).resolve().parent / "kernel.json"
PIN_ENV = "BRAINSTEM_MCP_KERNEL_PIN"
KERNEL_ENVS = ("BRAINSTEM_MCP_KERNEL", "RAPPMCP_KERNEL")
CACHE_ENVS = ("BRAINSTEM_MCP_CACHE", "RAPPMCP_CACHE")
BASE_PATH = "rapp_brainstem/agents/basic_agent.py"


class KernelError(RuntimeError):
    pass


def check_pin(pin):
    """None when the pin names one basic_agent.py by commit and sha256, else a plain reason."""
    if not isinstance(pin, dict):
        return "the kernel pin is not an object"
    if not isinstance(pin.get("repository"), str) or not pin["repository"].startswith("https://github.com/"):
        return "the kernel pin needs a GitHub repository URL"
    commit = pin.get("commit")
    if not (isinstance(commit, str) and len(commit) == 40 and all(c in "0123456789abcdef" for c in commit)):
        return "the kernel pin needs a full 40-character commit"
    files = pin.get("files")
    if not isinstance(files, list) or not any(
        isinstance(f, dict) and str(f.get("path", "")).endswith("agents/basic_agent.py")
        and isinstance(f.get("sha256"), str) and len(f["sha256"]) == 64 for f in files
    ):
        return "the kernel pin needs agents/basic_agent.py with its sha256"
    return None


def read_pin(path=None):
    """The pin in force: the file named by BRAINSTEM_MCP_KERNEL_PIN (or path), else kernel.json."""
    source = path or os.environ.get(PIN_ENV) or PACKAGED_PIN
    data = json.loads(pathlib.Path(source).read_text(encoding="utf-8"))
    pin = data.get("kernel") if isinstance(data.get("kernel"), dict) else data
    problem = check_pin(pin)
    if problem:
        raise KernelError(f"{source}: {problem}")
    return pin


def _apply(pin):
    global PIN, BASE, INSTALLED
    PIN = pin
    BASE = next(f for f in PIN["files"] if f["path"].endswith("agents/basic_agent.py"))
    INSTALLED = pathlib.Path(os.path.expanduser((PIN.get("installed") or {}).get(
        "path", "~/.brainstem/src/rapp_brainstem/agents/basic_agent.py")))


def use_pin(path):
    """Use the pin in this file (a kernel pin, or a distro manifest) here and in every worker."""
    path = str(pathlib.Path(path).resolve())
    _apply(read_pin(path))
    os.environ[PIN_ENV] = path


_apply(read_pin())


def _env(names):
    for name in names:
        if os.environ.get(name):
            return os.environ[name]
    return None


def cache_dir():
    """Where a fetched kernel file is kept: $BRAINSTEM_MCP_CACHE, else $XDG_CACHE_HOME/brainstem-mcp,
    else ~/.cache/brainstem-mcp. One folder per pinned commit."""
    base = _env(CACHE_ENVS) or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "brainstem-mcp"
    )
    return pathlib.Path(base) / "kernel" / PIN["commit"]


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def pinned_url():
    repo = PIN["repository"].replace("https://github.com/", "")
    return f"https://raw.githubusercontent.com/{repo}/{PIN['commit']}/{BASE['path']}"


def _fetch_pinned():
    target = cache_dir() / "basic_agent.py"
    if target.exists() and sha256(target) == BASE["sha256"]:
        return target
    with urllib.request.urlopen(pinned_url(), timeout=20) as reply:
        data = reply.read()
    if hashlib.sha256(data).hexdigest() != BASE["sha256"]:
        raise KernelError("The kernel file from GitHub does not match the pin. Nothing was loaded.")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Several workers may fetch at once: write a private file, then swap it in whole.
    part = target.with_name(f".{target.name}.{os.getpid()}.part")
    part.write_bytes(data)
    os.replace(part, target)
    return target


def locate():
    """Return the path of a basic_agent.py whose bytes match the pin.

    In order: $BRAINSTEM_MCP_KERNEL (set by --kernel), the installed Brainstem's copy,
    the cached copy, then a fresh fetch from GitHub at the pinned commit.
    """
    override = _env(KERNEL_ENVS)
    if override:
        path = pathlib.Path(os.path.expanduser(override))
        if not path.is_file():
            raise KernelError(f"--kernel points at {path}, which is not a file.")
        if sha256(path) != BASE["sha256"]:
            print(f"[brainstem-mcp] warning: {path} is not the pinned kernel file", file=sys.stderr)
        return path
    if INSTALLED.is_file() and sha256(INSTALLED) == BASE["sha256"]:
        return INSTALLED
    try:
        return _fetch_pinned()
    except KernelError:
        raise
    except Exception as error:
        raise KernelError(
            "Could not find the pinned basic_agent.py. Install a Brainstem, or connect to the "
            f"internet once so it can be fetched ({error})."
        ) from error


_installed_class = None


def install(agent_dir=None):
    """Register agents.basic_agent and openrappter.agents.basic_agent. Returns BasicAgent."""
    global _installed_class
    if _installed_class is None:
        path = locate()
        # Run it from the bytes just read, and never through the import system: that way
        # nothing (no __pycache__, no .pyc) is ever written into the kernel install.
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != BASE["sha256"] and not _env(KERNEL_ENVS):
            raise KernelError(f"{path} changed after it was checked. Nothing was loaded.")
        module = types.ModuleType("agents.basic_agent")
        module.__file__ = str(path)
        sys.modules["agents.basic_agent"] = module
        exec(compile(data, str(path), "exec"), module.__dict__)
        _installed_class = module.BasicAgent
        agents_pkg = sys.modules.get("agents") or types.ModuleType("agents")
        agents_pkg.__path__ = []
        agents_pkg.basic_agent = module
        sys.modules["agents"] = agents_pkg
        sys.modules["agents.basic_agent"] = module
        rapper = types.ModuleType("openrappter")
        rapper.__path__ = []
        rapper_agents = types.ModuleType("openrappter.agents")
        rapper_agents.__path__ = []
        rapper_agents.basic_agent = module
        rapper.agents = rapper_agents
        sys.modules.setdefault("openrappter", rapper)
        sys.modules.setdefault("openrappter.agents", rapper_agents)
        sys.modules.setdefault("openrappter.agents.basic_agent", module)
    if agent_dir:
        # Same as a Brainstem: `from agents.other_agent import X` finds sibling files.
        path = str(agent_dir)
        if path not in sys.modules["agents"].__path__:
            sys.modules["agents"].__path__.insert(0, path)
        _storage_shims(pathlib.Path(agent_dir))
    return _installed_class


def _storage_shims(agent_dir):
    """Inside a Brainstem, agents import its local storage as utils.azure_file_storage,
    utils.dynamics_storage and utils.storage_factory. Register the same shims the Brainstem
    does when the agents folder sits in one (its parent has local_storage.py). The file is
    run from its bytes, so nothing is written next to it."""
    storage = agent_dir.parent / "local_storage.py"
    if "utils.azure_file_storage" in sys.modules or not storage.is_file():
        return
    module = types.ModuleType("local_storage")
    module.__file__ = str(storage)
    sys.modules["local_storage"] = module
    try:
        exec(compile(storage.read_bytes(), str(storage), "exec"), module.__dict__)
        manager = module.AzureFileStorageManager
    except Exception as error:
        sys.modules.pop("local_storage", None)
        print(f"[brainstem-mcp] could not load {storage}: {error}", file=sys.stderr)
        return
    utils = sys.modules.get("utils") or types.ModuleType("utils")
    utils.__path__ = getattr(utils, "__path__", [])
    sys.modules["utils"] = utils
    for name, attr, value in (("azure_file_storage", "AzureFileStorageManager", manager),
                              ("dynamics_storage", "DynamicsStorageManager", manager),
                              ("storage_factory", "get_storage_manager", lambda: manager())):
        shim = types.ModuleType(f"utils.{name}")
        setattr(shim, attr, value)
        sys.modules[f"utils.{name}"] = shim
        setattr(utils, name, shim)
