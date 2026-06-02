from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


def make_capture_graph() -> tuple[
    MagicMock, list[dict[str, Any]], list[dict[str, Any]]
]:
    """Create a mock graph that captures inputs and kwargs sent to ``astream_events``."""
    captured_inputs: list[dict[str, Any]] = []
    captured_kwargs: list[dict[str, Any]] = []

    async def capture_astream_events(graph_input: dict, **kwargs: Any):
        captured_inputs.append(dict(graph_input))
        captured_kwargs.append(dict(kwargs))
        return
        yield  # make it an async generator

    mock_graph = MagicMock()
    mock_graph.astream_events = capture_astream_events
    return mock_graph, captured_inputs, captured_kwargs
