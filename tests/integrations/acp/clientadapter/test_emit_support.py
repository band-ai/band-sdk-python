"""Which ``Emit`` kinds the ACP client adapter declares."""

from __future__ import annotations

import pytest

from band.core.exceptions import BandConfigError
from band.core.types import Emit
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
)


class TestACPClientAdapterEmitSupport:
    """Which ``Emit`` kinds the ACP client adapter declares.

    All three ACP adapters (OMP / Copilot / Cursor) inherit this: room
    narration is gated by the caller's ``emit=`` (see ``room_emitter``),
    and kinds ACP cannot observe (``Emit.USAGE``) are rejected up front
    instead of silently ignored.
    """

    def test_supported_emit_kinds_are_accepted(self) -> None:
        adapter = ACPClientAdapter(
            ACPClientAdapterConfig(command=["omp", "acp"]),
            emit=Emit.TOOL_CALLS | Emit.THOUGHTS | Emit.TASK_EVENTS,
        )
        assert adapter.features.emit == frozenset(
            {Emit.TOOL_CALLS, Emit.THOUGHTS, Emit.TASK_EVENTS}
        )

    def test_silence_is_accepted(self) -> None:
        adapter = ACPClientAdapter(
            ACPClientAdapterConfig(command=["omp", "acp"]), emit=()
        )
        assert adapter.features.emit == frozenset()

    def test_an_unsupported_emit_kind_is_rejected(self) -> None:
        with pytest.raises(BandConfigError):
            ACPClientAdapter(
                ACPClientAdapterConfig(command=["omp", "acp"]), emit=Emit.USAGE
            )
