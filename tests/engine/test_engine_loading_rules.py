"""The engine loads agents the way the Brainstem does."""
import pytest

from conftest import EXAMPLES, FIXTURES, connect, text_of, agent_tools
from brainstem_mcp import loader

RULES = FIXTURES / "rules"


def test_brainstem_rules():
    agents, where, quarantined, errors = loader.load(RULES)
    assert set(agents) == {"Alpha", "Beta", "Broken"}
    assert where["Alpha"].endswith("alpha_agent.py")
    reasons = {(q["class"], q["reason"].split(" (")[0]) for q in quarantined}
    assert ("BadName", "name 'bad name!' is not tool-safe") in reasons
    assert ("TwinTwo", "duplicate agent name 'Twin' within one file") in reasons
    assert ("AlphaAgain", "duplicate agent name 'Alpha'; already registered by an earlier file") in reasons
    assert "Hidden" not in agents and "Twin" not in agents
    assert errors == []


@pytest.mark.anyio
async def test_named_file_serves_only_that_file():
    async with connect(EXAMPLES / "echo_agent.py") as (session, _):
        names = [t.name for t in agent_tools(await session.list_tools())]
        assert names == ["Echo"]
        result = await session.call_tool("Echo", {"text": "hi", "shout": True})
        assert text_of(result) == "HI" and not result.is_error


@pytest.mark.anyio
async def test_system_context_becomes_server_instructions():
    async with connect(EXAMPLES) as (_, init):
        assert "Echo says text back" in (init.instructions or "")


@pytest.mark.anyio
async def test_state_lives_on_disk_across_calls(tmp_path):
    async with connect(EXAMPLES, env={"NOTES_DATA_DIR": str(tmp_path)}) as (session, _):
        await session.call_tool("Notes", {"action": "add", "note": "first"})
        await session.call_tool("Notes", {"action": "add", "note": "second"})
        result = await session.call_tool("Notes", {"action": "list"})
    assert text_of(result) == "1. first\n2. second"
