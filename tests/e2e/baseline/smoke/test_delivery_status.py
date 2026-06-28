"""Live coverage of the per-recipient delivery-status lifecycle.

Exercises the real ``message_updated`` delivery states the platform emits — no
mocked payloads, no rigged returns. One healthy agent drives the success path
(``processing`` -> ``processed``); one deliberately-failing agent drives the
``failed`` path. Together with the unit tests in
``tests/websocket/test_reply_capture_delivery.py`` (which cover the parsing,
history and retry logic against the real payload shape) this covers every
``DeliveryStatus`` value.

Run with:

    E2E_TESTS_ENABLED=true uv run pytest \
        tests/e2e/baseline/smoke/test_delivery_status.py -v -s --no-cov
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest

from band.client.streaming import DeliveryStatus
from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import PlatformMessage

from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.waiting import ReplyCapture

logger = logging.getLogger(__name__)

CaptureFactory = Callable[[str], AbstractAsyncContextManager[ReplyCapture]]


class _FailingAdapter(SimpleAdapter[Any]):
    """An agent that always raises while handling a message.

    The runtime catches the exception and marks the message ``failed`` on the
    platform (see execution._process_event), giving us a real ``FAILED``
    delivery state to observe — no provider key needed, it never reaches an LLM.
    """

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: Any,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        raise RuntimeError("intentional failure: e2e coverage of the FAILED state")


@pytest.mark.asyncio(loop_scope="session")
async def test_healthy_message_reaches_processed_via_processing(
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    anthropic_adapter: SimpleAdapter,
) -> None:
    """Success path: the observed lifecycle ends in PROCESSED and passes through
    PROCESSING, and the agent's reply is already captured once PROCESSED lands."""
    async with running_provisioned_agent(
        anthropic_adapter, resource_manager, label="healthy"
    ) as (_, agent):
        room_id = await resource_manager.provision_room(
            title="e2e-delivery-healthy", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id, "Say hi.", mention_id=agent.id, mention_name=agent.name
            )
            await capture.wait_for_processed(mid, agent.id)
            history = capture.delivery_history(mid, agent.id)
            replied = any(m.sender_id == agent.id for m in capture.messages)

    logger.info("healthy delivery history: %s", [s.value for s in history])
    assert history, "expected at least one delivery-status transition"
    assert history[-1] is DeliveryStatus.PROCESSED, f"history ended at {history}"
    assert DeliveryStatus.PROCESSING in history, f"never saw processing: {history}"
    # The barrier's core guarantee: processed implies the reply is already in.
    assert replied, "PROCESSED reported but no agent reply was captured"


@pytest.mark.asyncio(loop_scope="session")
async def test_failing_agent_reaches_failed_state(
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Failure path: a genuinely-raising agent drives a real FAILED delivery
    state. No provider key needed — the adapter raises before any LLM call."""
    async with running_provisioned_agent(
        _FailingAdapter(), resource_manager, label="failing"
    ) as (_, agent):
        room_id = await resource_manager.provision_room(
            title="e2e-delivery-failing", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id, "This will fail.", mention_id=agent.id, mention_name=agent.name
            )
            reached = await capture.wait_for_delivery(
                mid, agent.id, until={DeliveryStatus.FAILED}
            )
            history = capture.delivery_history(mid, agent.id)

    logger.info("failing delivery history: %s", [s.value for s in history])
    assert reached is DeliveryStatus.FAILED
    assert DeliveryStatus.FAILED in history
