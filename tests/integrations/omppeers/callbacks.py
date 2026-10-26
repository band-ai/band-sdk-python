"""Script host-tool, UI and event-delivery interactions."""

from __future__ import annotations

import os
from typing import cast

from pydantic import JsonValue

from band.integrations.ompwire import (
    OmpEventType,
    OmpFrameType,
)
from tests.integrations.ompframes import (
    Frame,
    send,
    send_approval_request,
    send_host_tool_request,
)
from tests.integrations.omppeers.base import COMPLETE, RpcPeer
from tests.integrations.ompscenarios import PeerCommand, PeerScenario

HOST_CANCELLED_ID = "host-cancelled"
UI_CANCELLED_ID = "ui-cancelled"
SETTLED_CALLBACK_ID = "host-held"
LATE_CALLBACK_ID = "host-late"


def burst_message_id(number: int) -> str:
    return f"burst-{number}"


class CallbackPeer(RpcPeer):
    SCENARIOS = frozenset(
        {
            PeerScenario.PARALLEL_CALLBACKS,
            PeerScenario.SETTLED_DURING_CALLBACK,
            PeerScenario.HOST_CANCEL,
            PeerScenario.UI_CANCEL,
            PeerScenario.UI_ERROR,
            PeerScenario.UI_NOTIFY,
            PeerScenario.REUSED_CALLBACK,
            PeerScenario.EVENT_BACKPRESSURE,
            PeerScenario.CALLBACK_CONTROLS,
        }
    )

    def __init__(self, scenario: PeerScenario) -> None:
        super().__init__()
        if scenario not in self.SCENARIOS:
            raise ValueError(f"Unsupported {type(self).__name__} script: {scenario}")
        self.scenario = scenario
        self.tool_results: set[str] = set()
        self.ui_answered = False

    def prompt(self, frame: Frame) -> None:
        self.begin_prompt(frame)
        if self.is_follow_up(frame):
            self.complete_prompt(frame)
            return
        if self.scenario == PeerScenario.PARALLEL_CALLBACKS:
            self.request_parallel_callbacks(frame)
            return
        self.start_agent_prompt(frame)
        match self.scenario:
            case PeerScenario.SETTLED_DURING_CALLBACK:
                send_host_tool_request(
                    SETTLED_CALLBACK_ID, "call-held", "band_chat", {}
                )
                self.complete_agent_turn(COMPLETE)
            case PeerScenario.HOST_CANCEL:
                send_host_tool_request(
                    HOST_CANCELLED_ID, "call-cancelled", "band_chat", {}
                )
            case PeerScenario.UI_CANCEL | PeerScenario.UI_ERROR:
                send_approval_request(UI_CANCELLED_ID)
            case PeerScenario.UI_NOTIFY:
                send(
                    {
                        "type": OmpFrameType.UI_REQUEST,
                        "id": "ui-notify",
                        "method": "notify",
                        "message": "status",
                    }
                )
                self.complete_agent_turn(COMPLETE)
            case PeerScenario.REUSED_CALLBACK:
                self.repeat_host_tool_request()
            case PeerScenario.EVENT_BACKPRESSURE:
                return
            case PeerScenario.CALLBACK_CONTROLS:
                self.complete_agent_turn(COMPLETE)

    def respond_with_state(self, frame: Frame) -> None:
        super().respond_with_state(frame)
        if self.held is None:
            return
        match (self.scenario, self.held.type):
            case (PeerScenario.SETTLED_DURING_CALLBACK, PeerCommand.CHECKPOINT):
                self.checkpoint(self.held)
                self.held = None
            case (PeerScenario.EVENT_BACKPRESSURE, PeerCommand.EVENT_RESULT):
                self.respond(self.held)
                self.held = None

    def handle_command(self, frame: Frame) -> None:
        match frame.type:
            case PeerCommand.EVENT_BURST:
                self.send_event_burst(frame)
            case PeerCommand.EVENT_RESULT:
                self.held = frame
                self.emit_assistant_turn(COMPLETE, compacted=True)
                self.send_prompt_result(settled=True)
                self.settle_session()
            case PeerCommand.EVENT_UI:
                self.held = frame
                send_approval_request("backlog-approval")
            case PeerCommand.REPEAT_CALLBACK:
                self.repeat_host_tool_request()
                self.respond(frame)
            case PeerCommand.ASK_UI:
                self.request_structured_question(frame)
            case PeerCommand.RELEASE_PROMPT:
                send_host_tool_request(
                    LATE_CALLBACK_ID, f"call-{LATE_CALLBACK_ID}", "band_chat", {}
                )
                self.respond(frame)
            case PeerCommand.CANCEL_HOST:
                self.cancel_host_tool(frame)
            case PeerCommand.CANCEL_UI:
                self.cancel_ui_request(frame)
            case OmpFrameType.HOST_TOOL_RESULT | OmpFrameType.UI_RESPONSE:
                self.receive_callback_result(frame)
            case PeerCommand.ORPHAN_TOOL:
                send_host_tool_request("host-orphan", "call-orphan", "band_chat", {})
            case PeerCommand.ORPHAN_UI:
                send_approval_request("ui-orphan")
                self.respond(frame)
            case PeerCommand.ORPHAN_UI_CANCEL:
                send(
                    {
                        "type": OmpFrameType.UI_REQUEST,
                        "id": "ui-cancel-orphan",
                        "method": "cancel",
                    }
                )
            case _:
                super().handle_command(frame)

    def checkpoint(self, frame: Frame) -> None:
        if (
            self.scenario == PeerScenario.SETTLED_DURING_CALLBACK
            and self.prompt_id is not None
            and not self.state_requested
        ):
            self.held = frame
            return
        self.respond(
            frame,
            {
                "pid": os.getpid(),
                "frames": self.frames,
                "snapshots": self.snapshot_responses,
            },
        )

    @staticmethod
    def repeat_host_tool_request() -> None:
        send_host_tool_request("host-dup", "call-dup", "band_chat", {})

    def request_parallel_callbacks(self, frame: Frame) -> None:
        self.held = frame
        send({"type": OmpEventType.AGENT_START})
        for number in (1, 2):
            send_host_tool_request(
                f"host-{number}", f"call-{number}", "band_chat", {"number": number}
            )
        send_approval_request("approval-1")

    def send_event_burst(self, frame: Frame) -> None:
        for number in range(frame.count):
            send(
                {
                    "type": OmpEventType.MESSAGE_UPDATE,
                    "messageId": burst_message_id(number),
                    "message": {"role": "assistant", "content": str(number)},
                }
            )
        self.respond(frame)

    def request_structured_question(self, frame: Frame) -> None:
        self.held = frame
        send(
            {
                "type": OmpFrameType.UI_REQUEST,
                "id": "structured-question",
                "method": "ask",
                "questions": [
                    {
                        "id": "database-choice",
                        "question": "Which database?",
                        "options": [{"label": "SQLite"}],
                    }
                ],
            }
        )

    def cancel_host_tool(self, frame: Frame) -> None:
        send(
            {
                "type": OmpFrameType.HOST_TOOL_CANCEL,
                "id": "cancel-frame",
                "targetId": HOST_CANCELLED_ID,
            }
        )
        self.complete_agent_turn("host cancelled")
        self.respond(frame)

    def cancel_ui_request(self, frame: Frame) -> None:
        send(
            {
                "type": OmpFrameType.UI_REQUEST,
                "id": "ui-cancel-frame",
                "method": "cancel",
                "targetId": UI_CANCELLED_ID,
            }
        )
        self.complete_agent_turn("ui cancelled")
        self.respond(frame)

    def receive_callback_result(self, frame: Frame) -> None:
        match frame.type:
            case OmpFrameType.HOST_TOOL_RESULT:
                if frame.id is not None:
                    self.tool_results.add(frame.id)
            case OmpFrameType.UI_RESPONSE:
                self.ui_answered = True
                self.respond_to_ui_probe(frame)
        self.complete_after_parallel_callbacks()

    def respond_to_ui_probe(self, frame: Frame) -> None:
        if self.held is None:
            return
        match (frame.id, self.held.type):
            case ("backlog-approval", PeerCommand.EVENT_UI) if (
                self.scenario == PeerScenario.EVENT_BACKPRESSURE
            ):
                self.respond(self.held, {"value": frame.value})
                self.held = None
            case ("structured-question", PeerCommand.ASK_UI):
                answer = cast(
                    dict[str, JsonValue], frame.model_dump(exclude_unset=True)
                )
                self.respond(self.held, {"answer": answer})
                self.held = None

    def complete_after_parallel_callbacks(self) -> None:
        if (
            self.scenario == PeerScenario.PARALLEL_CALLBACKS
            and self.tool_results == {"host-1", "host-2"}
            and self.ui_answered
            and self.held is not None
        ):
            self.respond(self.held)
            self.held = None
            self.complete_agent_turn("callbacks complete")
