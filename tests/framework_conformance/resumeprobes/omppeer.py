"""Serve a native OMP seeding prompt that replies or fails."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from band.integrations.ompwire import OmpPromptStatus
from tests.framework_conformance.outcomeprobes import REPLY
from tests.framework_conformance.resumeprobes.probe import SeedOutcome
from tests.framework_conformance.turnprobes import TurnScript
from tests.framework_conformance.turnprobes.omppeer import TurnPeer
from tests.integrations.ompframes import Frame
from tests.integrations.omppeer import announce_ready
from tests.integrations.ompscenarios import PeerScenario


class SeedPeer(TurnPeer):
    def __init__(self, outcome: SeedOutcome, plugin: Path) -> None:
        super().__init__(TurnScript((REPLY,)), plugin)
        self.outcome = outcome

    def run_prompt(self, frame: Frame) -> None:
        if self.outcome == SeedOutcome.CONFIRMED:
            super().run_prompt(frame)
            return
        self.begin_prompt(frame)
        self.start_agent_prompt(frame)
        self.emit_assistant_turn("", stop=OmpPromptStatus.ERROR)
        self.send_prompt_result(settled=True, status=OmpPromptStatus.ERROR)
        self.settle_session()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("outcome", type=SeedOutcome)
    parser.add_argument("--plugin-dir", type=Path, required=True)
    args, _native_options = parser.parse_known_args()
    peer = SeedPeer(args.outcome, args.plugin_dir)
    announce_ready(PeerScenario.STANDARD_TURN)
    for line in sys.stdin:
        peer.handle(Frame.model_validate_json(line, strict=True))


if __name__ == "__main__":
    main()
