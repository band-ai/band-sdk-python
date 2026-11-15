"""Script independent UI decisions and native events over the existing RPC peer."""

from __future__ import annotations

import argparse
import sys
from enum import StrEnum
from pathlib import Path

from pydantic import Field, JsonValue
from pydantic_settings import BaseSettings, SettingsConfigDict

from band.integrations.omp import OMP_FORM_APPROVE, OMP_FORM_DENY
from band.integrations.ompwire import OmpEventType, OmpFrameType, OmpUIMethod
from tests.integrations.ompframes import Frame, send
from tests.integrations.omppeers.startup import StartupPeer

NATIVE_EVENT_ID = "ui-wire-id"
DENIED_PATH = "denied.txt"
NATIVE_FAILURE_OUTPUT = "Native tool failed"
NATIVE_SUCCESS_OUTPUT = "Native tool finished"
PERMISSION_AFTER_NATIVE = "permission-after-native"


class PermissionOrder(StrEnum):
    NATIVE_FIRST = "native-first"
    PERMISSION_FIRST = "permission-first"


class PermissionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OMP_TEST_",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )
    permission_title: str
    native_name: str = "read"
    native_arguments: dict[str, JsonValue] = Field(
        default_factory=lambda: {"path": DENIED_PATH}
    )
    native_error: bool = True


class PermissionPeer(StartupPeer):
    def __init__(self, order: PermissionOrder, plugin: Path | None) -> None:
        super().__init__(plugin)
        self.order = PermissionOrder(order)
        self.settings = PermissionSettings()

    def native_start(self) -> None:
        send(
            {
                "type": OmpEventType.TOOL_START,
                "toolCallId": NATIVE_EVENT_ID,
                "toolName": self.settings.native_name,
                "args": self.settings.native_arguments,
            }
        )

    def native_end(self) -> None:
        send(
            {
                "type": OmpEventType.TOOL_END,
                "toolCallId": NATIVE_EVENT_ID,
                "toolName": self.settings.native_name,
                "isError": self.settings.native_error,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                NATIVE_FAILURE_OUTPUT
                                if self.settings.native_error
                                else NATIVE_SUCCESS_OUTPUT
                            ),
                        }
                    ]
                },
            }
        )

    def ask_ui(self) -> None:
        send(
            {
                "type": OmpFrameType.UI_REQUEST,
                "id": NATIVE_EVENT_ID,
                "method": OmpUIMethod.SELECT,
                "title": self.settings.permission_title,
                "options": [OMP_FORM_APPROVE, OMP_FORM_DENY],
            }
        )

    def run_prompt(self, frame: Frame) -> None:
        self.begin_prompt(frame)
        self.respond(frame)
        send({"type": "agent_start"})
        if self.order == PermissionOrder.NATIVE_FIRST:
            self.native_start()
        else:
            self.ask_ui()

    def handle(self, frame: Frame) -> None:
        if frame.type == PERMISSION_AFTER_NATIVE:
            self.respond(frame)
            self.ask_ui()
            return
        if frame.type == OmpFrameType.UI_RESPONSE:
            if frame.id != NATIVE_EVENT_ID:
                raise ValueError(f"Unexpected UI response id: {frame.id}")
            if self.order == PermissionOrder.PERMISSION_FIRST:
                self.native_start()
            self.native_end()
            self.emit_assistant_turn("")
            self.send_prompt_result(settled=True)
            self.settle_session()
            return
        super().handle(frame)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("order", type=PermissionOrder, choices=tuple(PermissionOrder))
    parser.add_argument("--plugin-dir", type=Path)
    args, _native_options = parser.parse_known_args()
    peer = PermissionPeer(args.order, args.plugin_dir)
    send(
        {
            "type": "ready",
            "protocolVersion": 1,
            "supportedProtocolVersions": [1, 2],
            "maxFrameBytes": 1024 * 1024,
            "maxReassembledFrameBytes": 64 * 1024 * 1024,
        }
    )
    for line in sys.stdin:
        peer.handle(Frame.model_validate_json(line, strict=True))


if __name__ == "__main__":
    main()
