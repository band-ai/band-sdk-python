"""Exercise model selection through actual RPC permission callbacks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import JsonValue

from band.integrations.ompwire import OmpCommandName
from tests.integrations.ompframes import Frame
from tests.integrations.omppeer import announce_ready
from tests.integrations.omppermissionpeer import PermissionOrder, PermissionPeer
from tests.integrations.ompscenarios import PeerScenario


class SelectionPeer(PermissionPeer):
    def __init__(self, plugin: Path | None) -> None:
        super().__init__(PermissionOrder.PERMISSION_FIRST, plugin)
        self.model: dict[str, JsonValue] = {"provider": "openai", "id": "original"}
        self.thinking = "low"

    def state_snapshot(self) -> dict[str, JsonValue]:
        return {
            **super().state_snapshot(),
            "model": self.model,
            "thinkingLevel": self.thinking,
        }

    def handle_command(self, frame: Frame) -> None:
        match frame.type:
            case OmpCommandName.SET_MODEL:
                self.model = {"provider": frame.provider, "id": frame.modelId}
                self.respond(frame)
            case OmpCommandName.SET_THINKING_LEVEL:
                self.thinking = frame.level
                self.respond(frame)
            case OmpCommandName.GET_AVAILABLE_THINKING_LEVELS:
                self.respond(frame, {"levels": ["off", "low", "high"]})
            case _:
                super().handle_command(frame)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-dir", type=Path)
    args, _native_options = parser.parse_known_args()
    peer = SelectionPeer(args.plugin_dir)
    announce_ready(PeerScenario.STANDARD_TURN)
    for line in sys.stdin:
        peer.handle(Frame.model_validate_json(line, strict=True))


if __name__ == "__main__":
    main()
