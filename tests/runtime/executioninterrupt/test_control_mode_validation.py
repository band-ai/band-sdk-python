"""Validating the control mode an interrupt carries."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.client.streaming import ControlMode
from band.runtime.execution import ExecutionContext


class TestControlModeValidation:
    """interrupt()'s kind argument is typed ControlMode | str -- a plain
    string still coerces, but an invalid or wrong-for-this-method value must
    be rejected at the typed boundary rather than silently misbehaving."""

    @pytest.mark.parametrize(
        "kind",
        [
            pytest.param("bogus", id="not-a-control-mode-member"),
            pytest.param(
                ControlMode.PLAY,
                id="play-is-a-valid-member-but-wrong-for-interrupt",
            ),
            pytest.param("play", id="play-as-a-plain-string"),
        ],
    )
    async def test_rejects_invalid_or_wrong_kind(self, mock_link, kind):
        ctx = ExecutionContext("room-123", mock_link, AsyncMock(), agent_id="agent-123")

        with pytest.raises(ValueError):
            ctx.interrupt(kind=kind)
