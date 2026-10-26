# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""Choose which band-sdk releases a Fern docs review covers, and which PRs it supersedes.

Reads ``gh pr list --json number,headRefName`` output for merged and open Fern
docs PRs. Writes ``{"baseline": <tag>, "supersede": [<PR number>...]}``: the
review covers ``baseline..TAG``, and ``supersede`` lists the open docs PRs
whose release that range already covers.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from collectprs import git, tag_version
from pydantic import BaseModel, ConfigDict, TypeAdapter
from pydantic.alias_generators import to_camel
from releasedata import SDK_TAG_PREFIX

DOCS_BRANCH_PREFIX = "docs/python-sdk-"
# A review never reaches further back than this many band-sdk releases.
MAX_DOCS_RELEASES = 3


class DocsPr(BaseModel):
    model_config = ConfigDict(
        frozen=True, alias_generator=to_camel, validate_by_name=True
    )

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


def docs_range(
    tag: str, tags: Sequence[str], merged: Sequence[DocsPr], open_prs: Sequence[DocsPr]
) -> DocsRange:
    """Start after the newest documented release below ``tag``, at most ``MAX_DOCS_RELEASES`` back.

    ``supersede`` lists the open docs PRs for releases strictly inside the range.
    """
    version = tag_version(tag)
    if version is None:
        raise ValueError(f"{tag!r} is not a {SDK_TAG_PREFIX}X.Y.Z tag")
    earlier = sorted(
        (found for name in tags if (found := tag_version(name)) and found < version),
        reverse=True,
    )
    if not earlier:
        raise ValueError(f"no {SDK_TAG_PREFIX}* release precedes {tag}")
    floor = earlier[min(MAX_DOCS_RELEASES, len(earlier)) - 1]
    documented = [
        release
        for pr in merged
        if (release := pr.release) and floor <= release < version
    ]
    baseline = max(documented, default=floor)
    return DocsRange(
        baseline=f"{SDK_TAG_PREFIX}{'.'.join(map(str, baseline))}",
        supersede=[
            pr.number
            for pr in open_prs
            if (release := pr.release) and baseline < release < version
        ],
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("merged", type=Path, help="gh pr list --state merged output")
    parser.add_argument("open", type=Path, help="gh pr list --state open output")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    tags = git(args.repo, "tag", "--list", f"{SDK_TAG_PREFIX}*").split()
    merged = DOCS_PRS.validate_json(args.merged.read_bytes())
    open_prs = DOCS_PRS.validate_json(args.open.read_bytes())
    found = docs_range(args.tag, tags, merged, open_prs)
    sys.stdout.write(found.model_dump_json() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
