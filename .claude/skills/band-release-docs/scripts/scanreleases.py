# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""Select the oldest recent SDK release without an announcement."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from collectprs import is_announced, tag_version
from githubcli import gh_output
from pydantic import BaseModel, TypeAdapter
from releasedata import LOOKBACK_DAYS
from stepoutputs import append_outputs

logger = logging.getLogger(__name__)


class Release(BaseModel):
    tag_name: str
    published_at: datetime | None
    draft: bool
    prerelease: bool


RELEASES = TypeAdapter(list[list[Release]])


def scan(
    sdk: str,
    caller: str,
    run_id: int,
    *,
    now: datetime,
    github: Callable[..., str] = gh_output,
) -> str:
    pages = github("api", f"repos/{sdk}/releases", "--paginate", "--slurp")
    cutoff = now - timedelta(days=LOOKBACK_DAYS)
    releases = [
        release
        for page in RELEASES.validate_json(pages)
        for release in page
        if not release.draft
        and not release.prerelease
        and tag_version(release.tag_name) is not None
        and release.published_at is not None
        and cutoff <= release.published_at <= now
    ]
    for release in sorted(
        releases, key=lambda release: (release.published_at, release.tag_name)
    ):
        if not is_announced(release.tag_name, run_id, caller=caller, github=github):
            return release.tag_name
    return ""


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", required=True)
    parser.add_argument("--caller", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--outputs", type=Path)
    args = parser.parse_args(argv)
    try:
        tag = scan(args.sdk, args.caller, args.run_id, now=datetime.now(UTC))
    except (RuntimeError, ValueError):
        logger.error("Release scan failed; no release selected.")
        return 1
    append_outputs(args.outputs, tag=tag)
    if tag:
        sys.stdout.write(tag + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
