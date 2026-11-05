"""Expose dependency checking, operator preflight and authenticated serving."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from bandteams.evidence import Journal
from bandteams.preflight import dependencies, preflight
from bandteams.qualification import Evidence, decision
from bandteams.service import Service
from bandteams.settings import BandSettings, TeamsSettings, load_manifest
from bandteams.teams import application

logger = logging.getLogger(__name__)


async def serve(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    teams = TeamsSettings(_env_file=args.teams_credentials)
    band = BandSettings(_env_file=args.band_env)
    preflight(manifest, teams, band, args.state)
    if args.command == "preflight":
        logger.info("Local preflight passed; live qualification remains NOT RUN")
        return
    journal = Journal(args.state)
    app = application(teams)
    service = Service(app, manifest, band, journal, args.manifest)
    try:
        await app.start(teams.PORT)
    finally:
        await service.close()
        journal.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(
        description="Enterprise Teams qualification; transient Band credentials"
    )
    parser.add_argument(
        "command", choices=("check-dependencies", "preflight", "serve", "report")
    )
    parser.add_argument("--evidence", type=Path, default=Path("evidence.json"))
    parser.add_argument("--manifest", type=Path, default=Path("run.json"))
    parser.add_argument("--teams-credentials", type=Path, default=Path("teams.env"))
    parser.add_argument("--band-env", type=Path, default=Path(".env.test"))
    parser.add_argument("--state", type=Path, default=Path("state"))
    args = parser.parse_args()
    try:
        if args.command == "check-dependencies":
            logger.info("%s", json.dumps(dependencies(), sort_keys=True))
        elif args.command == "report":
            evidence = Evidence.model_validate_json(
                args.evidence.read_text(encoding="utf-8")
            )
            logger.info(
                "Qualification decision: %s; operator evidence requires review",
                decision(evidence, args.evidence.parent),
            )
        else:
            asyncio.run(serve(args))
    except ValidationError as error:
        logger.error(
            "BLOCKED: invalid or missing configuration fields: %s",
            [".".join(map(str, item["loc"])) for item in error.errors()],
        )
        raise SystemExit(2) from None
    except (ValueError, OSError):
        logger.error(
            "BLOCKED: preflight or state access failed; check supplied files and operator prerequisites"
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
