# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""Export shared release configuration to a workflow environment file."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from releasedata import (
    ALLOWED_BOTS,
    ANNOUNCED_MARKER_PREFIX,
    DOCS_STRUCTURE,
    FERN_BASE,
    FERN_REPO,
    HELD_ITEMS,
    MARKER_RETENTION_DAYS,
)
from stepoutputs import append_outputs


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", type=Path)
    args = parser.parse_args(argv)
    owner, repo = FERN_REPO.split("/")
    append_outputs(
        args.environment,
        ALLOWED_BOTS=ALLOWED_BOTS,
        FERN_OWNER=owner,
        FERN_REPO_NAME=repo,
        FERN_BASE=FERN_BASE,
        HELD_ITEMS=HELD_ITEMS,
        DOCS_STRUCTURE=DOCS_STRUCTURE,
        ANNOUNCED_MARKER_PREFIX=ANNOUNCED_MARKER_PREFIX,
        MARKER_RETENTION_DAYS=str(MARKER_RETENTION_DAYS),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
