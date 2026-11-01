"""An old native background terminal preserves held next-turn reporting."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest

from band.runtime.sessionconfig import SessionConfig
from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.activity import ActivityCapture
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.nativetasks import NativeTasks
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    running_agent_with_handle,
)
from tests.e2e.baseline.toolkit.resultexchange import ExchangeOperation, ResultExchange
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient

if TYPE_CHECKING:
    from band.adapters.claude_sdk import ClaudeSDKAdapter

BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=12)
PROMPT = (
    "Execute each requested command exactly once with the native Bash tool. "
    "Respect whether the request asks for background or foreground execution. "
    "Use band_send_message to reply in this room. Do not inspect, poll, or "
    "replace a requested command. The foreground result must be returned verbatim."
)
BASH = {"kind": "tool_call", "name": "Bash"}


class TestNativeReporting:
    @per_adapter(Adapter.CLAUDE_SDK)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_background_terminal_keeps_new_foreground_reporting(
        self,
        cell: AdapterCell,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        baseline_ws: TrackingWebSocketClient,
        result_exchange: ResultExchange,
        claude_native_tasks: NativeTasks,
        native_claude_adapter: Callable[[str], ClaudeSDKAdapter],
    ) -> None:
        identity = await cell.provision()
        room = await cell.resources.provision_room(
            title="e2e-native-background-activity", participants=[identity.id]
        )
        observed = ActivityCapture(deadline_s=BUDGET.deadline_s)
        with cell.resources.track_running(identity.id):
            async with (
                observed.rooms(baseline_ws, room),
                reply_capture(room, deadline_s=BUDGET.deadline_s) as replies,
                running_agent_with_handle(
                    identity,
                    native_claude_adapter(PROMPT),
                    cell.settings,
                    session_config=SessionConfig(
                        max_working_state_seconds=None, max_cycle_seconds=None
                    ),
                    link_factory=observed.link,
                ),
                result_exchange.releasing_on_exit(),
            ):
                beginning = observed.cursor()
                task_cursor = claude_native_tasks.cursor()
                first = await user_ops.send_message(
                    room,
                    "Run this command with Bash run_in_background=true. Once Bash "
                    "confirms it started, send a short started reply without waiting "
                    "for its output:\n"
                    + result_exchange.command(ExchangeOperation.PRODUCE),
                    mention_id=identity.id,
                    mention_name=identity.name,
                )
                await result_exchange.wait(
                    result_exchange.producer.entered,
                    phase="background producer entered",
                )
                background = await claude_native_tasks.started(room, since=task_cursor)
                await observed.detail(room, identity.id, BASH, since=beginning)
                await observed.report(
                    room,
                    agent_id=identity.id,
                    working=True,
                    activity=BASH,
                    since=beginning,
                )
                started_reply = await replies.wait_for_reply(first, identity.id)
                started_reply.assert_present()
                await replies.wait_for_processed(first, identity.id)

                foreground = observed.cursor()
                reply_cursor = replies.messages.snapshot()
                second = await user_ops.send_message(
                    room,
                    "Run this command once in foreground Bash (run_in_background=false). "
                    "Wait for its output, then send that exact complete output to this room:\n"
                    + result_exchange.command(ExchangeOperation.CONSUME),
                    mention_id=identity.id,
                    mention_name=identity.name,
                )
                await result_exchange.wait(
                    result_exchange.consumer.entered,
                    phase="new foreground consumer entered",
                )
                await observed.report(
                    room,
                    agent_id=identity.id,
                    working=True,
                    activity=BASH,
                    since=foreground,
                )

                result_exchange.producer.release.set()
                await result_exchange.wait(
                    result_exchange.committed,
                    phase="native producer committed its result",
                )
                terminal = await claude_native_tasks.terminal(background)
                assert terminal.status == "completed"
                after_terminal = observed.cursor()
                await observed.report(
                    room,
                    agent_id=identity.id,
                    working=True,
                    activity=BASH,
                    since=after_terminal,
                )

                finishing = observed.cursor()
                result_exchange.consumer.release.set()
                await result_exchange.wait(
                    result_exchange.consumer.settled,
                    phase="foreground native I/O settled",
                )
                result = await replies.wait_for_reply(
                    second, identity.id, since=reply_cursor
                )
                result.assert_contains_exact(result_exchange.result.decode("ascii"))
                await replies.wait_for_processed(second, identity.id)
                await observed.report(
                    room, agent_id=identity.id, working=False, since=finishing
                )
                await observed.stopped(room, identity.id, since=finishing)
                assert await replies.tool_calls(sender_id=identity.id) == []
                observed.assert_private(
                    result_exchange.note, result_exchange.result.decode("ascii")
                )
