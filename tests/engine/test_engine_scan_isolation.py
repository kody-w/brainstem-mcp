"""A bad agent file costs only its own tools, and never a slow startup."""
import shutil
import time

import pytest

from conftest import EXAMPLES, FIXTURES, connect, agent_tools

EXITS = {
    "sysexit_agent.py": "import sys\nsys.exit(3)\n",
    "osexit_agent.py": "import os\nos._exit(4)\n",
}


def serve_folder(tmp_path, extra):
    shutil.copy(EXAMPLES / "echo_agent.py", tmp_path / "echo_agent.py")
    shutil.copy(EXAMPLES / "notes_agent.py", tmp_path / "notes_agent.py")
    for name, body in extra.items():
        (tmp_path / name).write_text(body)
    return tmp_path


@pytest.mark.anyio
async def test_a_file_that_exits_at_import_loses_only_its_own_tools(tmp_path):
    folder = serve_folder(tmp_path, EXITS)
    log = tmp_path / "log.txt"
    with open(log, "w") as errlog:
        async with connect(folder, errlog=errlog) as (session, init):
            names = sorted(t.name for t in agent_tools(await session.list_tools()))
            assert "Echo says text back" in (init.instructions or "")
    assert names == ["Echo", "Notes"]
    text = log.read_text()
    assert "sysexit_agent.py" in text and "osexit_agent.py" in text


@pytest.mark.anyio
async def test_duplicates_across_files_are_still_quarantined_with_per_file_scans():
    async with connect(FIXTURES / "rules") as (session, _):
        names = sorted(t.name for t in agent_tools(await session.list_tools()))
    assert names == ["Alpha", "Beta", "Broken"]


@pytest.mark.anyio
async def test_a_file_that_hangs_at_import_is_skipped_and_startup_stays_fast(tmp_path):
    folder = serve_folder(tmp_path, {"hang_agent.py": "import time\ntime.sleep(600)\n"})
    log = tmp_path / "log.txt"
    with open(log, "w") as errlog:
        started = time.monotonic()
        async with connect(folder, "--scan-timeout", "3", errlog=errlog) as (session, _):
            initialized = time.monotonic() - started
            names = sorted(t.name for t in agent_tools(await session.list_tools()))
            listed = time.monotonic() - started
    assert initialized < 6, f"initialize took {initialized:.1f} s"
    assert listed < 10, f"tools/list took {listed:.1f} s"
    assert names == ["Echo", "Notes"]
    assert "skipped hang_agent.py" in log.read_text()


@pytest.mark.anyio
async def test_initialize_is_fast_even_with_the_default_scan_timeout(tmp_path):
    folder = serve_folder(tmp_path, {"hang_agent.py": "import time\ntime.sleep(600)\n"})
    started = time.monotonic()
    async with connect(folder) as (session, _):
        initialized = time.monotonic() - started
        names = sorted(t.name for t in agent_tools(await session.list_tools()))
        listed = time.monotonic() - started
    assert initialized < 6, f"initialize took {initialized:.1f} s"
    assert names == ["Echo", "Notes"]
    assert listed < 25, f"tools/list took {listed:.1f} s; keep it well under a client's startup wait"
