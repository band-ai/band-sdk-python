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
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from collectprs import SDK_TAG_PREFIX, tag_version
from pydantic import BaseModel, ConfigDict, TypeAdapter
from pydantic.alias_generators import to_camel

# Fern docs PRs for this SDK live on docs/python-sdk-<tag>.
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


def docs_baseline(tag: str, tags: Sequence[str], merged: Sequence[DocsPr]) -> str:
    """Return the newest documented release below ``tag``, floored ``MAX_DOCS_RELEASES`` back."""
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
        pr.release for pr in merged if pr.release and floor <= pr.release < version
    ]
    baseline = max(documented, default=floor)
    return f"{SDK_TAG_PREFIX}{'.'.join(map(str, baseline))}"


def superseded(tag: str, baseline: str, open_prs: Sequence[DocsPr]) -> list[int]:
    """Return the open docs PRs for releases strictly between ``baseline`` and ``tag``."""
    low, high = tag_version(baseline), tag_version(tag)
    if low is None or high is None:
        raise ValueError("baseline and tag must be release tags")
    return [pr.number for pr in open_prs if pr.release and low < pr.release < high]


def sdk_tags(repo: Path) -> list[str]:
    listing = subprocess.run(
        ["git", "-C", str(repo), "tag", "--list", f"{SDK_TAG_PREFIX}*"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return listing.split()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("merged", type=Path, help="gh pr list --state merged output")
    parser.add_argument("open", type=Path, help="gh pr list --state open output")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    merged = DOCS_PRS.validate_json(args.merged.read_bytes())
    open_prs = DOCS_PRS.validate_json(args.open.read_bytes())
    baseline = docs_baseline(args.tag, sdk_tags(args.repo), merged)
    result = {
        "baseline": baseline,
        "supersede": superseded(args.tag, baseline, open_prs),
    }
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
