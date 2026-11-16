"""A startup boundary that delivers input before start returns."""

from __future__ import annotations

from unittest.mock import MagicMock

from band.core.simple_adapter import SimpleAdapter
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from band.testing.platform import platform_connection_stub
from tests.conftest import make_message_event


class TrackingAdapter(SimpleAdapter):
    def __init__(self, call_order: list[str]):
        super().__init__()
        self.call_order = call_order
        self._system_prompt = ""

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        self.call_order.append("on_started")
        self._system_prompt = f"You are {agent_name}"

    async def on_message(
        self,
        msg,
        tools,
        history,
        participants_msg,
        contacts_msg,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        self.call_order.append("on_message")
        assert self._system_prompt != "", (
            "System prompt was empty during message processing! "
            "adapter.on_started() was not called before message arrived."
        )
        await tools.no_reply()


class ImmediateMessageRuntime:
    agent_name = "TestBot"
    agent_description = "A test bot"
    agent_id = "agent-123"
    connection = platform_connection_stub(agent_id="agent-123")
    feature_flags = None
    cleanup_pending = False
    session_config = SessionConfig()

    def __init__(self, rest):
        self.rest = rest

    async def initialize(self) -> None:
        """Initialize without starting message processing."""

    def claim_single_instance(self) -> None:
        pass

    def release_single_instance(self) -> None:
        pass

    async def start(
        self,
        on_execute,
        on_cleanup=None,
        on_control=None,
        on_message_control=None,
        on_execute_batch=None,
        is_message_control=None,
        on_execution_created=None,
    ):
        ctx = ExecutionContext(
            room_id="room-123",
            link=MagicMock(rest=self.rest),
            on_execute=on_execute,
            on_execution_created=on_execution_created,
            agent_id="agent-123",
            config=SessionConfig(enable_context_hydration=False),
        )

        await on_execute(
            ctx,
            make_message_event(room_id="room-123", msg_id="msg-1", content="Hello"),
        )

    async def stop(self, *, timeout: float) -> bool:
        return True

    async def run_forever(self, *, install_signal_handlers: bool = True):
        pass
