# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3", "pyyaml>=6,<7"]
# ///
"""Drop held PRs from the docs review's input, so held features stay undocumented.

A PR is dropped when a ledger entry for the ``sdks`` surface cites it, or when
it is a source of a held release item (``--held`` indexes into ``notes``). An
unreadable ledger fails closed. Prints the kept review input; logs counts only:
the ledger is private.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from holdbacks import HoldbacksError, LedgerEntry, load_ledger, read_model
from releasedata import ReleaseNotes, ReleasePrs

logger = logging.getLogger(__name__)


def withheld(
    review: ReleasePrs,
    notes: ReleaseNotes,
    held: Sequence[int],
    entries: Sequence[LedgerEntry],
) -> ReleasePrs:
    """``review`` without the PRs and commits a held item or ledger entry cites."""
    held_sources = {source for index in held for source in notes.items[index].sources}

    def is_held(source: str) -> bool:
        return source in held_sources or any(e.cites([source]) for e in entries)

    return review.model_copy(
        update={
            "pull_requests": [
                pr for pr in review.pull_requests if not is_held(str(pr.number))
            ],
            "loose_commits": [
                commit for commit in review.loose_commits if not is_held(commit.sha)
            ],
        }
    )


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path, help="collectprs.py's review range")
    parser.add_argument("notes", type=Path, help="the full release-notes.json")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument(
        "--held", type=int, nargs="*", default=[], help="held item indexes"
    )
    args = parser.parse_args(argv)

    try:
        review = read_model(ReleasePrs, args.review, "review PRs")
        notes = read_model(ReleaseNotes, args.notes, "release notes")
        entries = load_ledger(args.ledger)
    except HoldbacksError as exc:
        logger.error("Withholding failed closed: %s", exc)
        return 1
    kept = withheld(review, notes, args.held, entries)
    dropped = len(review.sources) - len(kept.sources)
    logger.info("Withheld %d of %d review source(s)", dropped, len(review.sources))
    sys.stdout.write(kept.model_dump_json(indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
