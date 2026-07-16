"""Helpers for reading emitted Emit.USAGE payloads back out of test doubles.

Both filter with ``is_usage_event`` (the single source of truth for "is this
a usage event") so tests don't re-derive the ``band_usage`` check the planned
first-class usage event would retire.
"""

from __future__ import annotations

from typing import Any

from band.core.types import USAGE_METADATA_KEY, is_usage_event
from band.testing import FakeAgentTools


def sent_usage_payloads(tools: Any) -> list[dict[str, Any]]:
    """Usage payloads from a mocked tools' awaited ``send_event`` calls.

    Takes the whole tools double, like its ``FakeAgentTools`` sibling below;
    typed ``Any`` because tests build it as a ``MagicMock``.
    """
    return [
        call.kwargs["metadata"][USAGE_METADATA_KEY]
        for call in tools.send_event.await_args_list
        if is_usage_event(call.kwargs.get("metadata"))
    ]


def recorded_usage_payloads(tools: FakeAgentTools) -> list[dict[str, Any]]:
    """Usage payloads recorded by a ``FakeAgentTools``."""
    return [
        event["metadata"][USAGE_METADATA_KEY]
        for event in tools.events_sent
        if is_usage_event(event["metadata"])
    ]
