# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""Choose which band-sdk releases a Fern docs review covers, and which PRs it supersedes.

Reads the merged and open Fern docs PR listings ``fernstate.py`` wrote into
its ``--out`` directory. Writes ``{"baseline": <tag>, "supersede": [<PR number>...]}``: the
review covers ``baseline..TAG``, and ``supersede`` lists the open docs PRs
whose release that range already covers. It refuses a TAG whose docs PR is
already merged, a TAG older than a release whose docs PR is merged (that review
would roll pages back), and a TAG older than an open docs PR, which covers it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from collectprs import (
    GhModel,
    earlier_sdk_tags,
    release_version,
    shape_errors,
    tag_version,
)
from fernstate import DOCS_MERGED_FILE, DOCS_OPEN_FILE
from pydantic import BaseModel, TypeAdapter, ValidationError
from releasedata import DOCS_BRANCH_PREFIX, SDK_TAG_PREFIX

logger = logging.getLogger(__name__)

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
    if version in newer:
        raise ValueError(f"docs for {tag} are already merged")
    if newer:
        raise ValueError(
            f"docs for {tag_name(newer[-1])} are already merged; "
            f"a review as of {tag} would roll their pages back"
        )
    pending = sorted(
        release for pr in open_prs if (release := pr.release) and release > version
    )
    if pending:
        raise ValueError(
            f"the open docs PR for {tag_name(pending[-1])} already covers {tag}"
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
    parser.add_argument("state", type=Path, help="fernstate.py's --out directory")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    try:
        release_version(args.tag)
        merged = DOCS_PRS.validate_json((args.state / DOCS_MERGED_FILE).read_bytes())
        open_prs = DOCS_PRS.validate_json((args.state / DOCS_OPEN_FILE).read_bytes())
        earlier = earlier_sdk_tags(args.repo, args.tag).values()
        found = docs_range(args.tag, list(earlier), merged, open_prs)
    except ValidationError as exc:
        logger.error(
            "A docs PR listing does not match its shape:\n%s", shape_errors(exc)
        )
        return 1
    except (RuntimeError, ValueError) as exc:
        logger.error("error: %s", exc)
        return 1
    sys.stdout.write(found.model_dump_json() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
