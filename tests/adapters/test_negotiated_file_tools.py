"""Regression coverage for adapters that cache their MCP tool registrations."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import Mock

import pytest

from band.adapters.letta import LettaAdapter
from band.adapters.opencode import OpencodeAdapter
from band.core.types import AdapterFeatures, Capability
from band.integrations.acp.client_adapter import ACPClientAdapter
from band.runtime.tools import FILE_TOOL_NAMES, ToolDefinition
from tests.e2e.baseline.toolkit.observations import ToolResult, ToolResults


def _opencode_definitions(adapter: object) -> list[ToolDefinition]:
    return adapter._tool_definitions  # type: ignore[attr-defined]


def _acp_definitions(adapter: object) -> list[ToolDefinition]:
    return adapter._tool_definitions  # type: ignore[attr-defined]


def _letta_definitions(adapter: object) -> list[ToolDefinition]:
    return adapter._mcp._tool_definitions  # type: ignore[attr-defined]


CachedAdapter = tuple[Callable[[], object], Callable[[object], list[ToolDefinition]]]


@pytest.mark.parametrize(
    ("build", "definitions"),
    [
        pytest.param(
            lambda: OpencodeAdapter(capabilities=Capability.FILES),
            _opencode_definitions,
            id="opencode",
        ),
        pytest.param(
            lambda: ACPClientAdapter(command="test-acp", capabilities=Capability.FILES),
            _acp_definitions,
            id="acp",
        ),
        pytest.param(
            lambda: LettaAdapter(capabilities=Capability.FILES),
            _letta_definitions,
            id="letta",
        ),
    ],
)
def test_feature_negotiation_rebuilds_cached_file_tool_registrations(
    build: Callable[[], object],
    definitions: Callable[[object], list[ToolDefinition]],
) -> None:
    """A disabled deployment must not retain tools cached before Agent.start()."""
    adapter = build()
    assert FILE_TOOL_NAMES <= {definition.name for definition in definitions(adapter)}

    adapter.apply_effective_features(AdapterFeatures())  # type: ignore[attr-defined]

    assert not FILE_TOOL_NAMES & {definition.name for definition in definitions(adapter)}


def test_tool_results_assert_succeeded_requires_a_successful_readback() -> None:
    """The file E2E must reject a narrated call whose platform operation failed."""
    results = ToolResults(
        [
            ToolResult(
                name="band_read_room_file",
                output='{"text": "file-marker"}',
                tool_call_id="read-1",
                is_error=False,
                raw=Mock(),
            )
        ]
    )

    results.assert_succeeded("band_read_room_file", output_contains="file-marker")

    failed = ToolResults(
        [
            ToolResult(
                name="band_read_room_file",
                output="file not found",
                tool_call_id="read-2",
                is_error=True,
                raw=Mock(),
            )
        ]
    )
    with pytest.raises(AssertionError, match="returned an error"):
        failed.assert_succeeded("band_read_room_file")
