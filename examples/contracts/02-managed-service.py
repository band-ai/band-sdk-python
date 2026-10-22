# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[contracts]>=6.0.1"]
# ///
"""Own a configured local service through readiness and confirmed shutdown."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from band import configure_logging
from band.contracts import (
    ContractAuthorService,
    ContractProgramService,
    ContractService,
    RuntimeConfig,
)

logger = logging.getLogger(__name__)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--service", choices=("contract", "author", "programs"), required=True
    )
    arguments = parser.parse_args()
    runtime = RuntimeConfig(python=arguments.python)
    if not arguments.config.is_absolute():
        raise ValueError(
            "Provide the absolute path to the private service configuration"
        )

    service: ContractService | ContractAuthorService | ContractProgramService
    match arguments.service:
        case "contract":
            await ContractService.check_config(arguments.config, runtime=runtime)
            service = ContractService(arguments.config, runtime=runtime)
        case "author":
            service = ContractAuthorService(arguments.config, runtime=runtime)
        case "programs":
            service = ContractProgramService(arguments.config, runtime=runtime)
        case _:
            raise ValueError("Choose a supported local service")

    async with service:
        logger.info("Service is ready; interrupt to close its owned process")
        await service.wait()


if __name__ == "__main__":
    configure_logging(logging.INFO)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Service shutdown completed")
