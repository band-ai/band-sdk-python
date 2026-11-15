"""A native model/session service, with explicit provider fault injection."""

from __future__ import annotations

import argparse
import sys
from enum import StrEnum
from pathlib import Path

from pydantic import JsonValue

from band.integrations.ompwire import OmpCommandName, OmpFrameType
from tests.integrations.ompframes import Frame, send, send_host_tool_request
from tests.integrations.omppeer import announce_ready
from tests.integrations.omppeers.startup import StartupPeer
from tests.integrations.ompscenarios import PeerCommand, PeerScenario


class NativeFault(StrEnum):
    EFFECTIVE_AUTO = "effective-auto"
    MISMATCH_EFFORT = "mismatch-effort"
    SWITCH_FAILURE = "switch-failure"
    SWITCH_CANCELLED = "switch-cancelled"
    NEW_SESSION_CANCELLED = "new-session-cancelled"


FAIL_MODEL_RESTORE = "fail-model-restore"
REPLY_REQUEST_ID = "native-reply"


class ModelPeer(StartupPeer):
    def __init__(
        self, plugin: Path | None, faults: list[NativeFault], session: str | None
    ) -> None:
        super().__init__(plugin)
        self.faults = set(faults)
        self.model: dict[str, JsonValue] = {"provider": "openai", "id": "original"}
        self.thinking = "auto"
        self.session_file = session
        self.restore_model: dict[str, JsonValue] | None = None

    def state_snapshot(self) -> dict[str, JsonValue]:
        level = self.thinking
        if NativeFault.EFFECTIVE_AUTO in self.faults and level == "auto":
            level = "high"
        if NativeFault.MISMATCH_EFFORT in self.faults and level != "auto":
            level = "off"
        return {
            **super().state_snapshot(),
            "model": self.model,
            "thinkingLevel": level,
            "sessionFile": self.session_file,
        }

    def run_prompt(self, frame: Frame) -> None:
        self.begin_prompt(frame)
        self.start_agent_prompt(frame)
        send_host_tool_request(
            REPLY_REQUEST_ID, "reply", "band_send_message", {"content": "Native reply"}
        )

    def handle_command(self, frame: Frame) -> None:
        match frame.type:
            case OmpCommandName.SET_MODEL:
                model: dict[str, JsonValue] = {
                    "provider": frame.provider,
                    "id": frame.modelId,
                }
                if model == self.restore_model:
                    self.reject(frame, "restore set_model failed")
                else:
                    self.model = model
                    self.respond(frame)
            case OmpCommandName.SET_THINKING_LEVEL:
                self.thinking = frame.level
                self.respond(frame)
            case OmpCommandName.GET_AVAILABLE_THINKING_LEVELS:
                self.respond(frame, {"levels": ["off", "low", "high"]})
            case OmpCommandName.SWITCH_SESSION:
                self.switch_session(frame)
            case OmpCommandName.NEW_SESSION:
                self.session_file = None
                self.respond(
                    frame,
                    {"cancelled": NativeFault.NEW_SESSION_CANCELLED in self.faults},
                )
            case OmpFrameType.HOST_TOOL_RESULT:
                if frame.id != REPLY_REQUEST_ID:
                    raise ValueError(f"Unexpected host tool result: {frame.id}")
                self.complete_agent_turn("")
            case command if command == FAIL_MODEL_RESTORE:
                self.restore_model = self.model.copy()
                self.respond(frame)
            case (
                OmpCommandName.NEGOTIATE_PROTOCOL
                | OmpCommandName.SET_ASK_DIALOG
                | OmpCommandName.SET_HOST_TOOLS
                | OmpCommandName.PROMPT
                | OmpCommandName.GET_STATE
                | OmpCommandName.GET_AVAILABLE_COMMANDS
                | OmpCommandName.ABORT
                | PeerCommand.CHECKPOINT
            ):
                super().handle_command(frame)
            case _:
                raise ValueError(f"Unsupported model peer command: {frame.type}")

    def switch_session(self, frame: Frame) -> None:
        if NativeFault.SWITCH_FAILURE in self.faults:
            self.reject(frame, "session missing")
            return
        cancelled = NativeFault.SWITCH_CANCELLED in self.faults
        if not cancelled:
            self.session_file = frame.sessionPath
        self.respond(frame, {"cancelled": cancelled})

    def reject(self, frame: Frame, message: str) -> None:
        send(
            {
                "type": OmpFrameType.RESPONSE,
                "id": frame.id,
                "command": frame.type,
                "success": False,
                "error": message,
            }
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-dir", type=Path)
    parser.add_argument("--fault", type=NativeFault, action="append", default=[])
    parser.add_argument("--session-file")
    args, _native_options = parser.parse_known_args()
    peer = ModelPeer(args.plugin_dir, args.fault, args.session_file)
    announce_ready(PeerScenario.STANDARD_TURN)
    for line in sys.stdin:
        peer.handle(Frame.model_validate_json(line, strict=True))


if __name__ == "__main__":
    main()
