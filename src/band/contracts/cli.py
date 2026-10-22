"""Launch the installed contract CLI with explicit interpreter qualification."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from band.contracts.errors import ContractError
from band.contracts.models import RuntimeConfig
from band.contracts.runner import QualifiedRunner

logger = logging.getLogger(__name__)


async def dispatch(python: Path, arguments: tuple[str, ...]) -> int:
    runner = QualifiedRunner(RuntimeConfig(python=python))
    await runner.check()
    os.execve(
        str(runner.configuration.python),
        runner.command("remote_agent.cli", arguments),
        runner.configuration.environment,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="band-contracts")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        return asyncio.run(dispatch(args.python, tuple(args.arguments)))
    except ContractError as error:
        logger.error("%s", error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
