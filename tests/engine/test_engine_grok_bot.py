"""What Grok Bot, the first user, needs from the tool list and the results."""
import re
import shutil

import pytest

from conftest import EXAMPLES, FIXTURES, agent_tools, connect, text_of
from brainstem_mcp import loader

HINTS = FIXTURES / "hints"
NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
FOLDERS = [EXAMPLES, FIXTURES / "rules", FIXTURES / "names", HINTS]

LONG = "L" * 65
LONG_AGENT = f'''
from agents.basic_agent import BasicAgent


class TooLong(BasicAgent):
    def __init__(self):
        super().__init__(name="{LONG}", metadata={{"name": "{LONG}", "description": "Too long a name."}})

    def perform(self, **kwargs):
        return "x"
'''


@pytest.mark.anyio
@pytest.mark.parametrize("folder", FOLDERS, ids=lambda p: p.name)
async def test_every_tool_has_a_safe_name_and_an_object_schema(folder):
    async with connect(folder) as (session, _):
        tools = agent_tools(await session.list_tools())
    assert tools
    for tool in tools:
        assert NAME.match(tool.name), tool.name
        assert tool.input_schema.get("type") == "object", tool.name


def test_a_name_over_64_characters_is_quarantined(tmp_path):
    shutil.copy(EXAMPLES / "echo_agent.py", tmp_path / "echo_agent.py")
    (tmp_path / "long_agent.py").write_text(LONG_AGENT)
    agents, _, quarantined, _ = loader.load(tmp_path)
    assert set(agents) == {"Echo"}
    assert any(q["class"] == "TooLong" and "64" in q["reason"] for q in quarantined)


@pytest.mark.anyio
async def test_read_only_agents_get_read_hints_and_others_get_none():
    async with connect(HINTS) as (session, _):
        tools = {t.name: t for t in agent_tools(await session.list_tools())}
    for name in ("Lookup", "Peek"):
        hints = tools[name].annotations
        assert hints is not None, name
        assert hints.read_only_hint is True and hints.destructive_hint is False, name
    assert tools["Plain"].annotations is None


@pytest.mark.anyio
async def test_json_answers_also_come_back_as_structured_content():
    async with connect(HINTS) as (session, _):
        lookup = await session.call_tool("Lookup", {"city": "Oslo"})
        plain = await session.call_tool("Plain", {})
    assert text_of(lookup) == '{"city": "Oslo", "temp": 21}'
    assert lookup.structured_content == {"city": "Oslo", "temp": 21}
    assert [b.type for b in lookup.content] == ["text"]
    assert text_of(plain) == "changed" and plain.structured_content is None
