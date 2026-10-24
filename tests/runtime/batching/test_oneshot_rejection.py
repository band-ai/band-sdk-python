"""A one-shot invocation drains one event at a time and never batches."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.core.exceptions import BandConfigError
from band.runtime.oneshot import OneShotInvoker, OneShotStatus
from band.runtime.sessionconfig import SessionConfig
from tests.runtime.conftest import make_link_mock, platform_msg
from tests.runtime.helpers import AGENT_ID
from tests.runtime.oneshot.kit import make_adapter_mock, msg_body


class TestOneShotRejection:
    def test_consuming_all_queued_is_refused(self) -> None:
        with pytest.raises(BandConfigError, match="one-shot"):
            OneShotInvoker(
                link=MagicMock(),
                adapter=MagicMock(),
                agent_id=AGENT_ID,
                session_config=SessionConfig(consume_all_queued=True),
            )

    async def test_a_session_config_that_leaves_batching_off_runs_the_event(
        self,
    ) -> None:
        adapter = make_adapter_mock()
        invoker = OneShotInvoker(
            link=make_link_mock(next_messages=[platform_msg("msg-1"), None]),
            adapter=adapter,
            agent_id=AGENT_ID,
            session_config=SessionConfig(),
        )
        await invoker.startup()

        result = await invoker.handle_event(msg_body(agent_id=AGENT_ID))

        assert (result["status"], adapter.received_input.msg.id) == (
            OneShotStatus.DONE,
            "msg-1",
        )
