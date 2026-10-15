"""Script independent UI decisions and native events over the existing RPC peer."""

from __future__ import annotations

import json
import os
import sys

from tests.integrations.omppeer import Frame, Peer, send


class PermissionPeer(Peer):
    def __init__(self, order: str) -> None:
        super().__init__("permission-records")
        self.order = order

    @staticmethod
    def native_start() -> None:
        send(
            {
                "type": "tool_execution_start",
                "toolCallId": "ui-wire-id",
                "toolName": os.environ.get("OMP_TEST_NATIVE_NAME", "read"),
                "args": json.loads(
                    os.environ.get("OMP_TEST_NATIVE_ARGUMENTS", '{"path":"denied.txt"}')
                ),
            }
        )

    @staticmethod
    def native_end() -> None:
        send(
            {
                "type": "tool_execution_end",
                "toolCallId": "ui-wire-id",
                "toolName": os.environ.get("OMP_TEST_NATIVE_NAME", "read"),
                "isError": os.environ.get("OMP_TEST_NATIVE_ERROR", "1") == "1",
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Native tool failed"
                                if os.environ.get("OMP_TEST_NATIVE_ERROR", "1") == "1"
                                else "Native tool finished"
                            ),
                        }
                    ]
                },
            }
        )

    @staticmethod
    def ask_ui() -> None:
        send(
            {
                "type": "extension_ui_request",
                "id": "ui-wire-id",
                "method": "select",
                "title": os.environ["OMP_TEST_PERMISSION_TITLE"],
                "options": ["Approve", "Deny"],
            }
        )

    def prompt(self, frame: Frame) -> None:
        self.prompt_id = frame.id
        self.settled = False
        self.respond(frame)
        send({"type": "agent_start"})
        if self.order == "native-first":
            self.native_start()
        else:
            self.ask_ui()

    def handle(self, frame: Frame) -> None:
        if frame.type == "permission-after-native":
            self.respond(frame)
            self.ask_ui()
            return
        if frame.type == "extension_ui_response":
            if frame.id != "ui-wire-id" or not frame.cancelled:
                raise AssertionError("The scripted UI decision must be cancelled")
            if self.order == "permission-first":
                self.native_start()
            self.native_end()
            self.end("")
            self.result(settled=True)
            self.settle()
            return
        super().handle(frame)


def main() -> None:
    peer = PermissionPeer(sys.argv[1])
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
