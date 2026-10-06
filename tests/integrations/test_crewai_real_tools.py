"""Platform tools built against the *real* crewai package.

Every other crewai test fakes `crewai.tools.BaseTool`, so none of them exercise the
pydantic model that a real `BaseTool` subclass actually is. That gap hid a failure
where building the tools raised
``PydanticUserError: `SendMessageTool` is not fully defined``.

Needs the dev-crewai venv:
    uv sync --extra dev-crewai
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("crewai", reason="crewai not installed (band-sdk[crewai])")

from band.core.types import AdapterFeatures, Capability
from band.integrations.crewai.tools import (
    CrewAIToolContext,
    NoopReporter,
    build_band_crewai_tools,
)
from band.runtime.tools import CHAT_TOOL_NAMES, AgentTools
from tests.identifiers import INVALID_IDS, UUID_ID
from tests.runtime.helpers import memory_client

# No capabilities: the base surface is exactly the chat tools.
EXPECTED_BASE_TOOLS = len(CHAT_TOOL_NAMES)


def _build() -> list[Any]:
    return build_band_crewai_tools(
        get_context=lambda: None,
        reporter=NoopReporter(),
        features=AdapterFeatures(),
    )


def test_platform_tools_build_against_the_real_base_tool() -> None:
    """The tool models resolve their annotations and instantiate."""
    tools = _build()

    assert len(tools) == EXPECTED_BASE_TOOLS
    assert "band_send_message" in {tool.name for tool in tools}


def test_file_tools_build_against_the_real_base_tool() -> None:
    """The three room-file tool models also resolve and instantiate for real --
    same "not fully defined" failure mode this file exists to catch."""
    tools = build_band_crewai_tools(
        get_context=lambda: None,
        reporter=NoopReporter(),
        features=AdapterFeatures(capabilities={Capability.FILES}),
    )

    names = {tool.name for tool in tools}
    assert {
        "band_list_room_files",
        "band_read_room_file",
        "band_send_room_file",
    }.issubset(names)


def test_a_mocked_crewai_window_does_not_poison_a_later_real_build() -> None:
    """Faking crewai for one test must leave the next one a working real package.

    The regression this guards: tests that mocked crewai also evicted the
    band.integrations.crewai modules and never restored them, after which the tool
    models could no longer resolve their annotations — pydantic resolves them through
    ``cls.__module__``, and that name no longer pointed at a live module.
    """
    fake_tools = MagicMock()
    fake_tools.BaseTool = type("BaseTool", (), {})

    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "crewai.tools", fake_tools)
        assert len(_build()) == EXPECTED_BASE_TOOLS

    assert len(_build()) == EXPECTED_BASE_TOOLS


@pytest.mark.parametrize("memory_id", [None, *INVALID_IDS, UUID_ID])
def test_real_crewai_memory_run_validates_before_rest(memory_id: str | None) -> None:
    async def run() -> None:
        async with memory_client() as (rest, requests):
            context = CrewAIToolContext(
                room_id="room-1", tools=AgentTools("room-1", rest)
            )
            built = build_band_crewai_tools(
                get_context=lambda: context,
                reporter=NoopReporter(),
                features=AdapterFeatures(capabilities={Capability.MEMORY}),
            )
            tool = next(t for t in built if t.name == "band_get_memory")
            args = {} if memory_id is None else {"memory_id": memory_id}
            result = json.loads(await asyncio.to_thread(tool._run, **args))
            if memory_id == UUID_ID:
                assert result["status"] == "success"
                assert result["id"] == UUID_ID
                assert [q.url.path for q in requests] == [
                    f"/api/v1/agent/memories/{UUID_ID}"
                ]
            else:
                assert result["status"] == "error"
                assert "memory_id" in result["message"]
                assert requests == []

    asyncio.run(run())
