# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""Choose which band-sdk releases a Fern docs review covers, and which PRs it supersedes.

Reads ``gh pr list --json number,headRefName`` output for merged and open Fern
docs PRs. Writes ``{"baseline": <tag>, "supersede": [<PR number>...]}``: the
review covers ``baseline..TAG``, and ``supersede`` lists the open docs PRs
whose release that range already covers. It refuses a TAG older than a release
whose docs PR is already merged, since that review would roll pages back.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from collectprs import GhModel, earlier_sdk_tags, release_version, tag_version
from pydantic import BaseModel, TypeAdapter
from releasedata import SDK_TAG_PREFIX

logger = logging.getLogger(__name__)

DOCS_BRANCH_PREFIX = "docs/python-sdk-"
# A review never reaches further back than this many band-sdk releases.
MAX_DOCS_RELEASES = 3


class DocsPr(GhModel):
    number: int
    head_ref_name: str

    @property
    def release(self) -> tuple[int, int, int] | None:
        """The release the branch documents; ``None`` for any other branch name."""
        if not self.head_ref_name.startswith(DOCS_BRANCH_PREFIX):
            return None
        return tag_version(self.head_ref_name.removeprefix(DOCS_BRANCH_PREFIX))


DOCS_PRS = TypeAdapter(list[DocsPr])


class DocsRange(BaseModel):
    baseline: str
    supersede: list[int]


def tag_name(version: tuple[int, int, int]) -> str:
    return f"{SDK_TAG_PREFIX}{'.'.join(map(str, version))}"


def docs_range(
    tag: str,
    earlier: Sequence[tuple[int, int, int]],
    merged: Sequence[DocsPr],
    open_prs: Sequence[DocsPr],
) -> DocsRange:
    """Start after the newest documented release below ``tag``, at most ``MAX_DOCS_RELEASES`` back.

    ``earlier`` holds the versions of the releases before ``tag``, at least one.
    ``supersede`` lists the open docs PRs for releases strictly inside the range.
    """
    version = release_version(tag)
    newer = sorted(
        release for pr in merged if (release := pr.release) and release >= version
    )
    if newer:
        raise ValueError(
            f"docs for {tag_name(newer[-1])} are already merged; "
            f"a review as of {tag} would roll their pages back"
        )
    floor = sorted(earlier, reverse=True)[:MAX_DOCS_RELEASES][-1]
    documented = [
        release for pr in merged if (release := pr.release) and release >= floor
    ]
    baseline = max(documented, default=floor)
    return DocsRange(
        baseline=tag_name(baseline),
        supersede=[
            pr.number
            for pr in open_prs
            if (release := pr.release) and baseline < release < version
        ],
    )


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("merged", type=Path, help="gh pr list --state merged output")
    parser.add_argument("open", type=Path, help="gh pr list --state open output")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    merged = DOCS_PRS.validate_json(args.merged.read_bytes())
    open_prs = DOCS_PRS.validate_json(args.open.read_bytes())
    try:
        release_version(args.tag)
        earlier = earlier_sdk_tags(args.repo, args.tag).values()
        found = docs_range(args.tag, list(earlier), merged, open_prs)
    except (RuntimeError, ValueError) as exc:
        logger.error("error: %s", exc)
        return 1
    sys.stdout.write(found.model_dump_json() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
