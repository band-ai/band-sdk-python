"""Terminal classification accepts raw known and future provider states."""

from __future__ import annotations

import pytest

from band.integrations.opencode.events import OpencodeToolStatus


class TestTerminalStatus:
    @pytest.mark.parametrize(
        ("status", "terminal"),
        [
            (OpencodeToolStatus.COMPLETED.value, True),
            (OpencodeToolStatus.ERROR.value, True),
            (OpencodeToolStatus.PENDING.value, False),
            (OpencodeToolStatus.RUNNING.value, False),
            ("future-provider-phase", False),
            (None, False),
        ],
    )
    def test_raw_status_is_classified_without_rejecting_unknown_values(
        self, status: str | None, terminal: bool
    ) -> None:
        assert OpencodeToolStatus.is_terminal(status) is terminal
