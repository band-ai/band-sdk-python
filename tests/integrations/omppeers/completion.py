"""Script prompt results, settlement evidence and completion faults."""

from __future__ import annotations

from pydantic import JsonValue

from band.integrations.ompwire import (
    OmpCommandName,
    OmpEventType,
    OmpFrameType,
    OmpPromptStatus,
)
from band.runtime.tools.types import BandTool
from tests.integrations.ompframes import (
    Frame,
    assistant_message,
    send,
    send_host_tool_request,
)
from tests.integrations.omppeers.base import (
    BACKGROUND_COMPLETE,
    COMPLETE,
    YIELDED,
    RpcPeer,
)
from tests.integrations.ompscenarios import PeerCommand, PeerScenario

COMPLETION_REPLY_CALL_ID = "recovered-reply"
COMPLETION_REPLY_RECIPIENT = "peer-456"


class CompletionPeer(RpcPeer):
    SCENARIOS = frozenset(
        {
            PeerScenario.YIELD_TO_BACKGROUND,
            PeerScenario.STALE_COMPLETION_BEFORE_YIELD,
            PeerScenario.FALSE_RESULT_SETTLED,
            PeerScenario.STATE_SETTLES,
            PeerScenario.SETTLES_DURING_STATE,
            PeerScenario.RESUME_ERROR,
            PeerScenario.RESUME_ERROR_COMPACTED,
            PeerScenario.RESUME_ABORTED,
            PeerScenario.LATE_PROMPT_ERROR,
            PeerScenario.STALE_IDLE_RESULT,
            PeerScenario.LOST_SETTLEMENT,
            PeerScenario.SILENT_SETTLED_CONTINUATION,
            PeerScenario.SETTLED_WITH_UNFINISHED_TOOL,
            PeerScenario.SETTLED_WITH_UNFINISHED_CUSTOM_TOOL,
            PeerScenario.SETTLED_WITH_PENDING,
            PeerScenario.DRAIN_BLOCKED_EVENT,
            PeerScenario.DRAIN_BLOCKED_BACKGROUND,
            PeerScenario.COMPLETION_REPLY,
            PeerScenario.REFRESH_STATE_SILENT,
            PeerScenario.GET_STATE_SILENT,
            PeerScenario.SETTLEMENT_BEFORE_RESULT,
            PeerScenario.NO_RESULT,
            PeerScenario.PENDING_NO_RESULT,
            PeerScenario.LATE_STALE_RESULT,
            PeerScenario.INITIAL_ERROR,
            PeerScenario.INITIAL_ABORTED,
        }
    )

    def __init__(self, scenario: PeerScenario) -> None:
        super().__init__()
        if scenario not in self.SCENARIOS:
            raise ValueError(f"Unsupported {type(self).__name__} script: {scenario}")
        self.scenario = scenario
        self.async_hint: bool | None = None
        self.drop_state_responses = False

    def prompt(self, frame: Frame) -> None:
        self.begin_prompt(frame)
        if self.is_follow_up(frame):
            if self.scenario == PeerScenario.LATE_STALE_RESULT:
                self.send_stale_prompt_result()
            self.complete_prompt(frame)
            return
        match self.scenario:
            case PeerScenario.STALE_IDLE_RESULT:
                self.complete_with_stale_result(frame)
            case PeerScenario.LOST_SETTLEMENT:
                self.yield_without_settlement(frame, async_hint=False)
            case PeerScenario.GET_STATE_SILENT:
                self.yield_without_settlement(frame, async_hint=None)
            case PeerScenario.SILENT_SETTLED_CONTINUATION:
                self.yield_after_prior_settlement(frame)
            case PeerScenario.SETTLED_WITH_UNFINISHED_TOOL:
                self.settle_before_tool_result(
                    frame,
                    request_id="host-stubborn",
                    tool_call_id="call-stubborn",
                    tool_name="band_chat",
                )
            case PeerScenario.SETTLED_WITH_UNFINISHED_CUSTOM_TOOL:
                self.settle_before_tool_result(
                    frame,
                    request_id="host-hold",
                    tool_call_id="call-hold",
                    tool_name="hold",
                )
            case PeerScenario.SETTLED_WITH_PENDING:
                self.contradict_settlement_with_pending_work(frame)
            case (
                PeerScenario.DRAIN_BLOCKED_EVENT | PeerScenario.DRAIN_BLOCKED_BACKGROUND
            ):
                self.emit_completion_after_update(
                    frame,
                    pending=self.scenario == PeerScenario.DRAIN_BLOCKED_BACKGROUND,
                )
            case PeerScenario.COMPLETION_REPLY:
                self.request_reply_and_settle(frame)
            case PeerScenario.REFRESH_STATE_SILENT | PeerScenario.LATE_STALE_RESULT:
                self.complete_prompt(frame)
            case _:
                if self.scenario == PeerScenario.STALE_COMPLETION_BEFORE_YIELD:
                    self.send_stale_completion()
                self.start_agent_prompt(frame)
                self.emit_yield()

    def emit_yield(self) -> None:
        match self.scenario:
            case PeerScenario.NO_RESULT | PeerScenario.SETTLEMENT_BEFORE_RESULT:
                self.emit_assistant_turn(COMPLETE)
                self.settle_session()
            case PeerScenario.PENDING_NO_RESULT:
                self.emit_assistant_turn(COMPLETE, pending=True)
            case PeerScenario.INITIAL_ERROR | PeerScenario.INITIAL_ABORTED:
                status = (
                    OmpPromptStatus.ERROR
                    if self.scenario == PeerScenario.INITIAL_ERROR
                    else OmpPromptStatus.ABORTED
                )
                self.emit_assistant_turn("failed", stop=status)
                self.send_prompt_result(settled=True, status=status)
                self.settle_session()
            case _:
                self.emit_assistant_turn(YIELDED, pending=True)
                self.send_prompt_result(
                    settled=self.scenario == PeerScenario.FALSE_RESULT_SETTLED
                )
                if self.scenario == PeerScenario.STATE_SETTLES:
                    self.settled = True

    def state_snapshot(self) -> dict[str, JsonValue]:
        state = super().state_snapshot()
        if self.async_hint is not None:
            state["hasPendingAsyncWork"] = self.async_hint
        return state

    def respond_with_state(self, frame: Frame) -> None:
        if self.scenario == PeerScenario.GET_STATE_SILENT or self.drop_state_responses:
            self.state_requested = True
            self.state_request_count += 1
            self.release_state_barriers()
            return
        super().respond_with_state(frame)
        if self.scenario == PeerScenario.SETTLES_DURING_STATE and not self.settled:
            self.settle_session()

    def handle_command(self, frame: Frame) -> None:
        match frame.type:
            case PeerCommand.DROP_STATE_RESPONSES:
                self.drop_state_responses = True
                self.respond(frame)
            case PeerCommand.RELEASE_PROMPT:
                self.release_prompt(frame)
            case _:
                super().handle_command(frame)

    def send_stale_prompt_result(self) -> None:
        send(
            {
                "type": OmpFrameType.PROMPT_RESULT,
                "id": "old-prompt",
                "agentInvoked": True,
                "status": "completed",
                "sessionSettled": True,
            }
        )

    def send_stale_completion(self) -> None:
        send({"type": OmpFrameType.SESSION_SETTLED})
        self.send_stale_prompt_result()

    def complete_with_stale_result(self, frame: Frame) -> None:
        self.start_agent_prompt(frame)
        self.emit_assistant_turn(COMPLETE)
        self.send_stale_prompt_result()
        self.settle_session()

    def yield_without_settlement(
        self, frame: Frame, *, async_hint: bool | None
    ) -> None:
        self.start_agent_prompt(frame)
        self.emit_assistant_turn(COMPLETE)
        self.async_hint = async_hint
        self.send_prompt_result(settled=False)

    def yield_after_prior_settlement(self, frame: Frame) -> None:
        self.start_agent_prompt(frame)
        self.emit_assistant_turn("first")
        self.settle_session()
        self.settled = False  # the continuation makes the session busy
        send({"type": OmpEventType.AGENT_START})
        self.emit_assistant_turn("continuation", pending=True)
        self.send_prompt_result(settled=False)

    def settle_before_tool_result(
        self, frame: Frame, *, request_id: str, tool_call_id: str, tool_name: str
    ) -> None:
        self.start_agent_prompt(frame)
        send_host_tool_request(request_id, tool_call_id, tool_name, {})
        self.complete_agent_turn(COMPLETE)

    def contradict_settlement_with_pending_work(self, frame: Frame) -> None:
        self.start_agent_prompt(frame)
        self.emit_assistant_turn("answer")
        self.send_prompt_result(settled=True)
        self.settled = True
        self.async_hint = True

    def emit_completion_after_update(self, frame: Frame, *, pending: bool) -> None:
        self.start_agent_prompt(frame)
        send(
            {
                "type": OmpEventType.MESSAGE_START,
                "messageId": COMPLETE,
                "message": assistant_message(""),
            }
        )
        send(
            {
                "type": OmpEventType.MESSAGE_UPDATE,
                "messageId": COMPLETE,
                "message": assistant_message(COMPLETE),
            }
        )
        self.emit_assistant_turn(COMPLETE, pending=pending)
        self.send_prompt_result(settled=not pending)
        if not pending:
            self.settle_session()

    def request_reply_and_settle(self, frame: Frame) -> None:
        self.start_agent_prompt(frame)
        send_host_tool_request(
            COMPLETION_REPLY_CALL_ID,
            COMPLETION_REPLY_CALL_ID,
            BandTool.SEND_MESSAGE,
            {"content": COMPLETE, "mentions": [COMPLETION_REPLY_RECIPIENT]},
        )
        self.complete_agent_turn(COMPLETE)

    def release_prompt(self, frame: Frame) -> None:
        match self.scenario:
            case PeerScenario.LATE_PROMPT_ERROR:
                self.fail_prompt_after_admission(
                    frame.failed_command or OmpCommandName.PROMPT
                )
            case PeerScenario.SETTLEMENT_BEFORE_RESULT:
                self.send_prompt_result(settled=False)
            case PeerScenario.DRAIN_BLOCKED_BACKGROUND | PeerScenario.PENDING_NO_RESULT:
                self.settle_session()
            case PeerScenario.SILENT_SETTLED_CONTINUATION:
                self.finish_background_without_settlement()
            case PeerScenario.SETTLED_WITH_PENDING:
                self.clear_pending_work()
            case _:
                self.resume_background_turn()
        self.respond(frame)

    def fail_prompt_after_admission(self, command: str) -> None:
        send(
            {
                "type": OmpFrameType.RESPONSE,
                "id": self.prompt_id,
                "command": command,
                "success": False,
                "error": "Prompt command failed after admission",
            }
        )

    def finish_background_without_settlement(self) -> None:
        send({"type": OmpEventType.AGENT_START})
        self.emit_assistant_turn(BACKGROUND_COMPLETE)
        self.settled = True

    def clear_pending_work(self) -> None:
        self.async_hint = None
        send({"type": OmpEventType.AGENT_START})
        self.emit_assistant_turn(BACKGROUND_COMPLETE)

    def resume_background_turn(self) -> None:
        send({"type": OmpEventType.AGENT_START})
        stop = {
            PeerScenario.RESUME_ERROR: OmpPromptStatus.ERROR,
            PeerScenario.RESUME_ERROR_COMPACTED: OmpPromptStatus.ERROR,
            PeerScenario.RESUME_ABORTED: OmpPromptStatus.ABORTED,
        }.get(self.scenario, "stop")
        self.emit_assistant_turn(
            BACKGROUND_COMPLETE,
            stop=stop,
            compacted=self.scenario == PeerScenario.RESUME_ERROR_COMPACTED,
        )
        self.settle_session()
