"""Kit: a manual-approval Codex adapter behind the real room scheduler."""

from __future__ import annotations

from band.adapters.codex import CodexAdapter, CodexCommand
from band.integrations.codex.types import CodexRequestMethod
from band.platform.event import ReconnectedEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools import BandTool
from tests.adapters.codex.kit import answered_turn, approval_tokens_from_chat
from tests.adapters.codexturns import (
    FakeCodexClient,
    tool_call_request,
    turn_completed,
)
from tests.adapters.lifecycle.runtime import ReplyingPlatform
from tests.runtime.conftest import Waiting, wait_for_condition

FINAL_REPLY = "Tests pass."
RELEASED_TURN = "turn-1"
# The turns that answer the held messages, one each, once the released turn ends.
HELD_TURNS = ("turn-2", "turn-3")


class ScheduledCodexRoom:
    """One room whose approved turn keeps running after its final reply."""

    def __init__(
        self,
        platform: ReplyingPlatform,
        adapter: CodexAdapter,
        client: FakeCodexClient,
        ctx: ExecutionContext,
    ) -> None:
        self.platform = platform
        self.adapter = adapter
        self.client = client
        self.ctx = ctx

    @property
    def processed(self) -> list[str]:
        return self.platform.marked("processed")

    @property
    def turn_starts(self) -> int:
        return self.client.request_methods.count(CodexRequestMethod.TURN_START)

    async def deliver(self, message_id: str, content: str) -> None:
        self.platform.add_message(message_id, content=content)
        await self.ctx.on_event(self.platform.event(message_id, content=content))

    async def processes(self, *message_ids: str) -> None:
        await wait_for_condition(lambda: self.processed == list(message_ids))

    async def open_window(self) -> None:
        """Approve the turn and let it post its final reply, without ending it."""
        await self.deliver("ask", "run tests")
        await self.processes("ask")
        token = approval_tokens_from_chat(self.platform.replies)[-1]
        await self.deliver("approve", f"/{CodexCommand.APPROVE} {token}")
        await self.processes("ask", "approve")
        self.client.emit_turn(
            RELEASED_TURN,
            tool_call_request(
                1,
                BandTool.SEND_MESSAGE,
                {"content": FINAL_REPLY, "mentions": ["@alice"]},
            ),
        )
        await wait_for_condition(lambda: FINAL_REPLY in self.platform.replies)

    async def hold(self, *message_ids: str) -> None:
        """Send ordinary messages that the still-running turn refuses."""
        for message_id in message_ids:
            await self.deliver(message_id, f"and {message_id}")
        await self._refused(message_ids[0])

    async def hold_missed(self, *message_ids: str) -> None:
        """Send ordinary messages the socket missed, refused by a reconnect's pass."""
        for message_id in message_ids:
            self.platform.add_message(message_id, content=f"and {message_id}")
        await self.ctx.on_event(ReconnectedEvent())
        await self._refused(message_ids[0])

    def end_turn(self) -> None:
        self.client.script(
            *(event for turn in HELD_TURNS for event in answered_turn(turn))
        )
        self.client.emit_turn(RELEASED_TURN, turn_completed(RELEASED_TURN))

    async def _refused(self, head: str) -> None:
        await wait_for_condition(lambda: self.ctx.scheduler.deferred_id == head)
        await Waiting.suspended(self.ctx)
