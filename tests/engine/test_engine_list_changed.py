"""Drop a file in and it is a tool. Take it out and it is gone."""
import shutil

import anyio
import mcp_types as types
import pytest

from conftest import EXAMPLES, connect, agent_tools

NEW_AGENT = '''
from agents.basic_agent import BasicAgent


class Wave(BasicAgent):
    def __init__(self):
        super().__init__(name="Wave", metadata={"name": "Wave", "description": "Wave hello."})

    def perform(self, **kwargs):
        return "hello"
'''


@pytest.mark.anyio
async def test_dropping_a_file_sends_tools_list_changed(tmp_path):
    shutil.copy(EXAMPLES / "echo_agent.py", tmp_path / "echo_agent.py")
    changed = anyio.Event()

    async def on_message(message):
        if isinstance(message, types.ToolListChangedNotification):
            changed.set()

    async with connect(tmp_path, "--poll", "0.2", on_message=on_message) as (session, init):
        assert init.capabilities.tools.list_changed is True
        assert [t.name for t in agent_tools(await session.list_tools())] == ["Echo"]

        (tmp_path / "wave_agent.py").write_text(NEW_AGENT)
        with anyio.fail_after(15):
            await changed.wait()
        assert sorted(t.name for t in agent_tools(await session.list_tools())) == ["Echo", "Wave"]
        assert (await session.call_tool("Wave", {})).content[0].text == "hello"

        changed = anyio.Event()
        (tmp_path / "wave_agent.py").unlink()
        with anyio.fail_after(15):
            await changed.wait()
        assert [t.name for t in agent_tools(await session.list_tools())] == ["Echo"]
