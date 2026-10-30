"""Conformance: for every agent in a folder, tools/list == metadata and tools/call == perform().

Drives the real server over stdio with the official MCP Python client.
"""
import pytest

from conftest import EXAMPLES, FIXTURES, agent_tools, connect, text_of
from brainstem_mcp import loader

CALL_FOLDERS = [EXAMPLES, FIXTURES / "rules", FIXTURES / "names"]
LIST_FOLDERS = CALL_FOLDERS


def sample(schema):
    """Arguments for every parameter, optional ones too, each with a value that is not the usual default.

    Sending only the required ones would let a server that drops optional arguments pass.
    """
    args = {}
    for key, prop in schema.get("properties", {}).items():
        if "enum" in prop:
            args[key] = prop["enum"][-1]
        else:
            args[key] = {"string": "hello", "integer": 2, "number": 1.5, "boolean": True,
                         "array": [], "object": {}}.get(prop.get("type"), "hello")
    return args


def direct(folder):
    agents, _, _, _ = loader.load(folder)
    return agents


@pytest.mark.anyio
@pytest.mark.parametrize("folder", LIST_FOLDERS, ids=lambda p: p.name)
async def test_tools_list_matches_metadata(folder):
    agents = direct(folder)
    assert agents, f"no agents in {folder}"
    async with connect(folder) as (session, _):
        listed = {t.name: t for t in agent_tools(await session.list_tools())}
    assert set(listed) == set(agents)
    for name, agent in agents.items():
        meta = agent.metadata
        tool = listed[name]
        # The kernel's to_tool() names the tool by the agent's own name, so that wins over metadata['name'].
        assert tool.name == agent.name == agent.to_tool()["function"]["name"]
        assert (tool.description or "") == meta.get("description", "")
        assert tool.input_schema == meta.get("parameters", {"type": "object", "properties": {}})


@pytest.mark.anyio
@pytest.mark.parametrize("folder", CALL_FOLDERS, ids=lambda p: p.name)
async def test_tools_call_matches_perform(folder, tmp_path, monkeypatch):
    served_data, direct_data = tmp_path / "served", tmp_path / "direct"
    monkeypatch.setenv("NOTES_DATA_DIR", str(direct_data))
    agents = direct(folder)
    expected = {}
    for name, agent in agents.items():
        args = sample(agent.metadata.get("parameters", {}))
        try:
            expected[name] = (args, False, str(agent.perform(**args)))
        except Exception as error:
            expected[name] = (args, True, f"Error: {error}")
    async with connect(folder, env={"NOTES_DATA_DIR": str(served_data)}) as (session, _):
        for name, (args, failed, text) in expected.items():
            result = await session.call_tool(name, args)
            assert bool(result.is_error) == failed, name
            assert text_of(result) == text, name


def test_names_fixture_really_differs():
    """The names fixture is the case where metadata['name'] and self.name differ."""
    agent = direct(FIXTURES / "names")["Greeter"]
    assert agent.metadata["name"] != agent.name


@pytest.mark.anyio
async def test_optional_arguments_reach_perform():
    async with connect(FIXTURES / "names") as (session, _):
        result = await session.call_tool("Greeter", {"who": "Ann", "excited": True, "times": 2, "style": "formal"})
    assert text_of(result) == "Good day Ann! Good day Ann!"
