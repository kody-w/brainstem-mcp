"""Contract: the engine uses the grail kernel unchanged.

The pinned kernel file at kernel.json's commit, the installed Brainstem's copy,
and the copy the engine actually loads must be byte-identical. brainstem-mcp keeps no
copy of its own. Mirrors brainstem-mcp's contract tests against the grail.
"""
import hashlib
import inspect
import json
import pathlib
import subprocess
import urllib.request

import pytest

from conftest import EXAMPLES, REPO, ROOT, agent_tools
from brainstem_mcp import kernel

# The pin ships inside the package. The root kernel.json is a link to it, which a Windows
# checkout may turn into a plain text file, so read the real one.
PIN = json.loads((ROOT / "brainstem_mcp" / "kernel.json").read_text())
FILE = PIN["files"][0]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def test_pin_is_well_formed():
    assert PIN["repository"] == "https://github.com/kody-w/rapp-installer"
    assert len(PIN["commit"]) == 40 and int(PIN["commit"], 16) >= 0
    assert FILE["path"] == "rapp_brainstem/agents/basic_agent.py" and len(FILE["sha256"]) == 64


def test_installed_kernel_matches_the_pin():
    if not kernel.INSTALLED.is_file():
        pytest.skip("no Brainstem installed on this machine")
    data = kernel.INSTALLED.read_bytes()
    assert digest(data) == FILE["sha256"] and len(data) == FILE["bytes"]


def test_grail_at_the_pinned_commit_matches_installed():
    try:
        with urllib.request.urlopen(kernel.pinned_url(), timeout=20) as reply:
            upstream = reply.read()
    except Exception as error:
        pytest.skip(f"GitHub not reachable: {error}")
    assert digest(upstream) == FILE["sha256"]
    if kernel.INSTALLED.is_file():
        assert upstream == kernel.INSTALLED.read_bytes()


def test_the_loaded_base_class_is_the_pinned_file():
    base = kernel.install()
    assert digest(open(inspect.getsourcefile(base), "rb").read()) == FILE["sha256"]
    assert base.__name__ == "BasicAgent"


def test_brainstem_mcp_keeps_no_copy_of_the_kernel():
    tracked = subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True).stdout.split()
    untracked = [str(p.relative_to(REPO)) for p in REPO.rglob("*.py") if ".venv" not in p.parts]
    for name in tracked + untracked:
        assert not name.endswith("basic_agent.py"), name
        assert not name.endswith("brainstem.py"), name


def test_serving_does_not_touch_the_kernel():
    if not kernel.INSTALLED.is_file():
        pytest.skip("no Brainstem installed on this machine")
    before = kernel.INSTALLED.stat()
    subprocess.run(
        [__import__("sys").executable, "-m", "brainstem_mcp.runner", "list", str(EXAMPLES)],
        cwd=str(ROOT), capture_output=True, check=True,
    )
    after = kernel.INSTALLED.stat()
    assert (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size)


def snapshot(folder):
    folder = pathlib.Path(folder)
    if not folder.exists():
        return {}
    return {str(p.relative_to(folder)): (p.stat().st_size, p.stat().st_mtime_ns) for p in folder.rglob("*")}


@pytest.mark.anyio
async def test_serving_writes_nothing_into_the_kernel_folder(tmp_path, monkeypatch):
    """No __pycache__, no .pyc, nothing: the engine only reads the kernel install."""
    from conftest import EXAMPLES, connect

    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    kernel_dir = tmp_path / "rapp_brainstem" / "agents"
    kernel_dir.mkdir(parents=True)
    copy = kernel_dir / "basic_agent.py"
    copy.write_bytes(kernel.locate().read_bytes())
    before = snapshot(kernel_dir.parent)
    async with connect(EXAMPLES, env={"BRAINSTEM_MCP_KERNEL": str(copy)}) as (session, _):
        await session.list_tools()
        await session.call_tool("Echo", {"text": "hi"})
    assert snapshot(kernel_dir.parent) == before


@pytest.mark.anyio
async def test_serving_writes_nothing_into_the_installed_kernel(monkeypatch):
    if not kernel.INSTALLED.is_file():
        pytest.skip("no Brainstem installed on this machine")
    from conftest import EXAMPLES, connect

    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    before = snapshot(kernel.INSTALLED.parent)
    async with connect(EXAMPLES) as (session, _):
        await session.list_tools()
        await session.call_tool("Echo", {"text": "hi"})
    assert snapshot(kernel.INSTALLED.parent) == before


@pytest.mark.anyio
async def test_kernel_flag_picks_the_kernel_file(tmp_path):
    from conftest import EXAMPLES, connect

    copy = tmp_path / "basic_agent.py"
    copy.write_bytes(kernel.locate().read_bytes() + b"\n# a local edit\n")
    log = tmp_path / "log.txt"
    with open(log, "w") as errlog:
        async with connect(EXAMPLES, "--kernel", str(copy), errlog=errlog) as (session, _):
            assert "Echo" in [t.name for t in agent_tools(await session.list_tools())]
    assert f"{copy} is not the pinned kernel file" in log.read_text()


def test_without_a_brainstem_the_pinned_file_is_fetched_and_checked(tmp_path):
    """A Linux box with no ~/.brainstem: fetch basic_agent.py at the pinned commit into a cache."""
    import os
    import sys

    try:
        urllib.request.urlopen(kernel.pinned_url(), timeout=10).close()
    except Exception as error:
        pytest.skip(f"GitHub not reachable: {error}")
    env = {k: v for k, v in os.environ.items() if k not in ("BRAINSTEM_MCP_KERNEL", "XDG_CACHE_HOME")}
    env.update(HOME=str(tmp_path / "home"), BRAINSTEM_MCP_CACHE=str(tmp_path / "cache"), PYTHONPATH=str(ROOT))
    found = subprocess.run(
        [sys.executable, "-c", "from brainstem_mcp import kernel; print(kernel.locate())"],
        env=env, cwd=str(tmp_path), capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert found.startswith(str(tmp_path / "cache"))
    assert digest(pathlib.Path(found).read_bytes()) == FILE["sha256"]
