from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.core.types import (
    Emit,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    final_text,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)
from tests.runtime.helpers import (
    ROOM_ID,
    deliver,
    run_through,
)
from tests.runtime.verdict.kit import (
    NATIVE_OUTCOMES,
    assert_runtime_outcome,
)


class TestCodexNativeTextDoesNotChangeTheRuntimeVerdict:
    @pytest.mark.parametrize("path", ["live", "backlog"])
    @pytest.mark.parametrize("thoughts", [True, False])
    @pytest.mark.parametrize("row", sorted(NATIVE_OUTCOMES))
    async def test_codex_native_text_does_not_change_the_runtime_verdict(
        self,
        link: MagicMock,
        path: str,
        thoughts: bool,
        row: str,
    ) -> None:
        calls, completes = NATIVE_OUTCOMES[row]
        events = [
            tool_call_request(index, name, arguments)
            for index, (name, arguments) in enumerate(calls, 1)
        ]
        adapter = make_codex_adapter(
            FakeCodexClient(
                events=[*events, final_text("Closing narration"), turn_completed()]
            ),
            emit={Emit.THOUGHTS} if thoughts else set(),
        )
        await adapter.on_started("Agent", "A coding agent")
        try:
            await deliver(run_through(adapter, link), path)
        finally:
            await adapter.on_cleanup(ROOM_ID)
        assert_runtime_outcome(link, completes)
