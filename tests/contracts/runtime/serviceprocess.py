"""Run the real service CLI with an explicit test clock and shutdown observation."""

from __future__ import annotations

import argparse
import threading
from contextlib import ExitStack
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import patch

from remote_agent.cli import main as service_main
from remote_agent.service import Service
from tests.contracts.runtime.providerpeer import reach_gate


class FileClock:
    def __init__(self, floor: int = 0, *, path: Path) -> None:
        self.floor = floor
        self.path = path

    def __call__(self) -> int:
        return max(self.floor, int(self.path.read_text(encoding="ascii")))


class ObservedService(Service):
    def __init__(
        self,
        config: dict[str, Any],
        *,
        stopping: threading.Event | None = None,
        stop_port: int,
        stop_token: str,
    ) -> None:
        self.stop_port = stop_port
        self.stop_token = stop_token
        self.stop_reported = False
        super().__init__(config, stopping=stopping)

    def request_stop(self) -> None:
        super().request_stop()
        if not self.stop_reported:
            reach_gate(self.stop_port, self.stop_token, wait_for_release=False)
            self.stop_reported = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("--clock", type=Path)
    parser.add_argument("--stop-port", type=int)
    parser.add_argument("--stop-token")
    args = parser.parse_args()
    with ExitStack() as resources:
        if args.clock is not None:
            resources.enter_context(
                patch(
                    "remote_agent.authority.RoomClock",
                    partial(FileClock, path=args.clock),
                )
            )
        if args.stop_port is not None:
            if args.stop_token is None:
                raise ValueError("A stop observation requires its process token")
            resources.enter_context(
                patch(
                    "remote_agent.cli.Service",
                    partial(
                        ObservedService,
                        stop_port=args.stop_port,
                        stop_token=args.stop_token,
                    ),
                )
            )
        return service_main(["serve", args.config])


if __name__ == "__main__":
    raise SystemExit(main())
