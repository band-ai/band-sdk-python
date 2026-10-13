"""A one-shot invocation drains one event at a time and never batches."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.core.exceptions import BandConfigError
from band.runtime.oneshot import OneShotInvoker
from band.runtime.types import SessionConfig


class TestOneShotRejection:
    def test_consuming_all_queued_is_refused(self) -> None:
        with pytest.raises(BandConfigError, match="one-shot"):
            OneShotInvoker(
                link=MagicMock(),
                adapter=MagicMock(),
                agent_id="agent-1",
                session_config=SessionConfig(consume_all_queued=True),
            )

    def test_the_default_session_config_is_accepted(self) -> None:
        OneShotInvoker(
            link=MagicMock(),
            adapter=MagicMock(),
            agent_id="agent-1",
            session_config=SessionConfig(),
        )
