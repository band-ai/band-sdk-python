"""codex advertises filtered central registry tools behavior."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from band.runtime.tools import BandTool
from band.runtime.tools.agent import AgentTools
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
)
from tests.runtime.helpers import (
    ROOM_ID,
)


class TestCodexAdvertisesFilteredCentralRegistryTools:
    @pytest.mark.parametrize(
        "selection, expected",
        [
            ({"exclude_tools": {BandTool.SEND_MESSAGE, BandTool.NO_REPLY}}, None),
            (
                {
                    "include_tools": {
                        BandTool.SEND_MESSAGE,
                        BandTool.NO_REPLY,
                        BandTool.GET_PARTICIPANTS,
                    }
                },
                {BandTool.SEND_MESSAGE, BandTool.NO_REPLY, BandTool.GET_PARTICIPANTS},
            ),
            (
                {
                    "include_categories": {"chat"},
                    "include_tools": {
                        BandTool.SEND_MESSAGE,
                        BandTool.NO_REPLY,
                        BandTool.GET_PARTICIPANTS,
                    },
                    "exclude_tools": {BandTool.NO_REPLY},
                },
                {BandTool.SEND_MESSAGE, BandTool.GET_PARTICIPANTS},
            ),
        ],
    )
    async def test_codex_advertises_filtered_central_registry_tools(
        self, link: MagicMock, selection: dict[str, Any], expected: set[str] | None
    ) -> None:
        tools = AgentTools(ROOM_ID, link.rest)
        adapter = make_codex_adapter(FakeCodexClient(), **selection)
        if expected is None:
            registry_names = {
                schema["function"]["name"]
                for schema in tools.get_openai_tool_schemas(
                    capabilities=adapter.features.capabilities
                )
            }
            expected = registry_names - {BandTool.SEND_MESSAGE, BandTool.NO_REPLY}
        assert {
            schema["name"] for schema in adapter._build_dynamic_tools(tools)
        } == expected
