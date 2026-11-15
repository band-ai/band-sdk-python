from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from band.platform.link import BandLink
from band.runtime.execution import (
    ExecutionContext,
)
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import (
    make_message_event,
)
from tests.runtime.helpers import LifecyclePlatform, rest_client_over
from tests.runtime.scheduler.driver import attempt_delivery


class TestTurnDiagnosticsDistinguishRunningHandlerFromAck:
    @pytest.mark.parametrize("cancel", [False, True], ids=["complete", "cancel"])
    async def test_turn_diagnostics_distinguish_running_handler_from_ack(
        self, caplog: pytest.LogCaptureFixture, cancel: bool
    ) -> None:
        peer = LifecyclePlatform()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            entered.set()
            await release.wait()

        caplog.set_level(logging.DEBUG, logger="band.runtime.delivery")
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id="agent-123", api_key="test")
            link.rest = rest
            ctx = ExecutionContext(
                "room-123",
                link,
                handler,
                config=SessionConfig(enable_context_hydration=False),
            )
            async with asyncio.TaskGroup() as tasks:
                task = tasks.create_task(
                    attempt_delivery(
                        ctx,
                        make_message_event(
                            room_id="room-123",
                            msg_id="observed-turn",
                            content="private prompt",
                        ),
                    )
                )
                await entered.wait()
                assert peer.marked("processing") == ["observed-turn"]
                assert peer.requested("processed") == []
                assert "phase=handler state=started" in caplog.text
                assert "phase=handler state=returned" not in caplog.text
                assert "phase=ack" not in caplog.text
                if cancel:
                    task.cancel()
                else:
                    release.set()

            if cancel:
                assert task.cancelled()
                assert peer.requested("processed") == []
                assert "phase=handler state=cancelled" in caplog.text
            else:
                assert peer.marked("processed") == ["observed-turn"]
                assert "phase=handler state=returned" in caplog.text
                assert (
                    "Ack room=room-123 message=observed-turn accepted=True"
                    in caplog.text
                )
            assert "private prompt" not in caplog.text
