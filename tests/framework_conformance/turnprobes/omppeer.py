"""Serve scripted model actions over native OMP RPC."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from band.integrations.ompwire import (
    OmpCommandName,
    OmpContentKind,
    OmpEventType,
    OmpFrameType,
    OmpPromptStatus,
)
from tests.framework_conformance.turnprobes import TurnScript
from tests.integrations.ompframes import Frame, send, send_host_tool_request
from tests.integrations.omppeer import announce_ready
from tests.integrations.omppeers.startup import StartupPeer
from tests.integrations.ompscenarios import PeerScenario


class TurnPeer(StartupPeer):
    def __init__(self, script: TurnScript, plugin: Path) -> None:
        super().__init__(plugin)
        self.script = script
        self.actions = iter(enumerate(script.tool_calls))
        self.pending_call: str | None = None
        self.pending_tool: tuple[str, str] | None = None

    def run_prompt(self, frame: Frame) -> None:
        self.begin_prompt(frame)
        self.start_agent_prompt(frame)
        self.next_action()

    def next_action(self) -> None:
        action = next(self.actions, None)
        if action is None:
            self.pending_call = None
            self.pending_tool = None
            self.complete_agent_turn(self.script.final_text)
            return
        index, call = action
        self.pending_call = f"request-{index}"
        call_id = f"tool-{index}"
        self.pending_tool = (call_id, call.name)
        send(
            {
                "type": OmpEventType.MESSAGE_END,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": OmpContentKind.TOOL_CALL,
                            "id": call_id,
                            "name": call.name,
                            "arguments": call.arguments,
                        }
                    ],
                },
            }
        )
        # Native reports tool execution on both sides of every host tool call;
        # the builtin decline guard proves the declining call is the only one
        # still running, so a peer that skipped these events could never decline.
        send(
            {
                "type": OmpEventType.TOOL_START,
                "toolCallId": call_id,
                "toolName": call.name,
                "args": call.arguments,
            }
        )
        send_host_tool_request(self.pending_call, call_id, call.name, call.arguments)

    def state_snapshot(self) -> dict[str, JsonValue]:
        state = super().state_snapshot()
        state["isStreaming"] = not self.settled
        state["hasPendingAsyncWork"] = False
        return state

    def abort_prompt(self, frame: Frame) -> None:
        self.respond(frame)
        if not self.settled:
            self.pending_call = None
            self.pending_tool = None
            self.emit_assistant_turn("", stop=OmpPromptStatus.ABORTED)
            self.send_prompt_result(settled=True, status=OmpPromptStatus.ABORTED)
            self.settle_session()

    def handle_command(self, frame: Frame) -> None:
        match frame.type:
            case OmpFrameType.HOST_TOOL_RESULT:
                if (
                    self.pending_call is None
                    or self.pending_tool is None
                    or frame.id != self.pending_call
                ):
                    raise ValueError(f"Unexpected host tool result: {frame.id}")
                call_id, tool_name = self.pending_tool
                send(
                    {
                        "type": OmpEventType.TOOL_END,
                        "toolCallId": call_id,
                        "toolName": tool_name,
                        "result": frame.result,
                        "isError": bool(frame.isError),
                    }
                )
                self.pending_call = None
                self.pending_tool = None
                self.next_action()
            case (
                OmpCommandName.GET_AVAILABLE_COMMANDS
                | OmpCommandName.SET_HOST_TOOLS
                | OmpCommandName.GET_STATE
                | OmpCommandName.NEGOTIATE_PROTOCOL
                | OmpCommandName.PROMPT
                | OmpCommandName.ABORT
            ):
                super().handle_command(frame)
            case _:
                raise ValueError(f"Unsupported turn peer command: {frame.type}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("script", type=Path)
    parser.add_argument("--plugin-dir", type=Path, required=True)
    args, _native_options = parser.parse_known_args()
    script = TypeAdapter(TurnScript).validate_json(args.script.read_bytes())
    peer = TurnPeer(script, args.plugin_dir)
    announce_ready(PeerScenario.STANDARD_TURN)
    for line in sys.stdin:
        peer.handle(Frame.model_validate_json(line, strict=True))


if __name__ == "__main__":
    main()
