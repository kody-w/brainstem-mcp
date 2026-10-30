"""A crashing, hanging or noisy agent cannot take the server down."""
import time

import pytest

from conftest import FIXTURES, connect, text_of

ROUGH = FIXTURES / "rough"


@pytest.mark.anyio
async def test_crash_is_an_error_and_the_server_lives():
    async with connect(ROUGH) as (session, _):
        result = await session.call_tool("Crash", {})
        assert result.is_error and "stopped without an answer" in text_of(result)
        assert text_of(await session.call_tool("Fine", {})) == "still here"


@pytest.mark.anyio
async def test_hang_times_out_and_the_server_lives():
    async with connect(ROUGH, "--timeout", "2") as (session, _):
        started = time.monotonic()
        result = await session.call_tool("Hang", {})
        assert time.monotonic() - started < 15
        assert result.is_error and "did not finish within 2 seconds" in text_of(result)
        assert text_of(await session.call_tool("Fine", {})) == "still here"


@pytest.mark.anyio
async def test_agent_output_cannot_corrupt_the_protocol():
    async with connect(ROUGH) as (session, _):
        result = await session.call_tool("Chatty", {})
        assert text_of(result) == "clean answer" and not result.is_error
        assert text_of(await session.call_tool("Fine", {})) == "still here"
