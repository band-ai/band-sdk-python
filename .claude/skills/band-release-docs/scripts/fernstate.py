# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""Record the Fern state a filing depends on, and decide what each part does.

Writes ``state.json`` (each branch's PR, the open PRs that also change the
release's date file) and the docs PR listings ``docsrange.py`` reads into
``--out``, and appends ``changelog`` and ``docs`` (``file``, ``reuse``, or
``skip``) to ``--outputs``. A merged branch PR is skipped and an open one is
reused. Fails when another open PR changes the date file the changelog would
write. Logs decisions and PR links only: the Fern repository is private.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable, Sequence
from enum import StrEnum
from pathlib import Path

from collectprs import GhModel, gh_output
from pydantic import BaseModel, TypeAdapter, ValidationError
from releasedata import (
    CHANGELOG_BRANCH_PREFIX,
    CHANGELOG_DIR,
    DOCS_BRANCH_PREFIX,
    FERN_REPO,
    REHEARSAL_SUFFIX,
    SDK_TAG_PREFIX,
    fern_branch,
    shape_errors,
)
from stepoutputs import append_outputs

logger = logging.getLogger(__name__)

Gh = Callable[..., str]

STATE_FILE = "state.json"
DOCS_MERGED_FILE = "docs-merged.json"
DOCS_OPEN_FILE = "docs-open.json"
# gh pr list stops at this many; more open Fern PRs would hide a conflict.
PR_LIST_LIMIT = "200"
DOCS_SEARCH = f"head:{DOCS_BRANCH_PREFIX}{SDK_TAG_PREFIX}"


class PrState(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    MERGED = "MERGED"


class Action(StrEnum):
    """What the filing does with one part (changelog or docs)."""

    FILE = "file"
    REUSE = "reuse"
    SKIP = "skip"


class BranchPr(GhModel):
    number: int
    state: PrState
    url: str


class PrFile(GhModel):
    path: str


class OpenPr(GhModel):
    number: int
    url: str
    head_ref_name: str
    changed_files: int
    files: list[PrFile]


class FernState(BaseModel):
    changelog_branch: str
    docs_branch: str
    changelog_pr: BranchPr | None
    docs_pr: BranchPr | None
    date_conflicts: list[str]


BRANCH_PRS = TypeAdapter(list[BranchPr])
OPEN_PRS = TypeAdapter(list[OpenPr])


def branch_pr(gh: Gh, branch: str) -> BranchPr | None:
    """The branch's merged PR, else its open one; a closed PR is filed again."""
    listing = gh(
        "pr",
        "list",
        "--repo",
        FERN_REPO,
        "--head",
        branch,
        "--state",
        "all",
        "--json",
        "number,state,url",
    )
    prs = BRANCH_PRS.validate_json(listing)
    for state in (PrState.MERGED, PrState.OPEN):
        found = [pr for pr in prs if pr.state is state]
        if found:
            return found[0]
    return None


def action(pr: BranchPr | None) -> Action:
    match pr:
        case BranchPr(state=PrState.MERGED):
            return Action.SKIP
        case BranchPr(state=PrState.OPEN):
            return Action.REUSE
        case _:
            return Action.FILE


def pr_files(gh: Gh, pr: OpenPr) -> list[str]:
    """Every file the PR changes; ``gh pr list`` stops at 100 per PR."""
    if pr.changed_files <= len(pr.files):
        return [file.path for file in pr.files]
    listing = gh(
        "api",
        f"repos/{FERN_REPO}/pulls/{pr.number}/files",
        "--paginate",
        "--jq",
        ".[].filename",
    )
    return listing.splitlines()


def date_conflicts(gh: Gh, date: str, own_branch: str) -> list[str]:
    """Open PRs, other than this release's branch and its twin, changing the date file."""
    listing = gh(
        "pr",
        "list",
        "--repo",
        FERN_REPO,
        "--state",
        "open",
        "--limit",
        PR_LIST_LIMIT,
        "--json",
        "number,url,headRefName,changedFiles,files",
    )
    base = own_branch.removesuffix(REHEARSAL_SUFFIX)
    own = {base, f"{base}{REHEARSAL_SUFFIX}"}
    date_file = f"{CHANGELOG_DIR}/{date}.md"
    return [
        pr.url
        for pr in OPEN_PRS.validate_json(listing)
        if pr.head_ref_name not in own and date_file in pr_files(gh, pr)
    ]


def docs_listing(gh: Gh, state: PrState) -> str:
    return gh(
        "pr",
        "list",
        "--repo",
        FERN_REPO,
        "--state",
        state.lower(),
        "--search",
        DOCS_SEARCH,
        "--limit",
        PR_LIST_LIMIT,
        "--json",
        "number,headRefName",
    )


def read_state(gh: Gh, tag: str, date: str | None, *, rehearsal: bool) -> FernState:
    """Read Fern; ``date`` is the release date, or ``None`` when no changelog is filed."""
    changelog_branch = fern_branch(CHANGELOG_BRANCH_PREFIX, tag, rehearsal=rehearsal)
    docs_branch = fern_branch(DOCS_BRANCH_PREFIX, tag, rehearsal=rehearsal)
    # Read even without a date: an open PR from an earlier run must not go unreported.
    changelog_pr = branch_pr(gh, changelog_branch)
    filing = date and action(changelog_pr) is not Action.SKIP
    return FernState(
        changelog_branch=changelog_branch,
        docs_branch=docs_branch,
        changelog_pr=changelog_pr,
        docs_pr=branch_pr(gh, docs_branch),
        date_conflicts=date_conflicts(gh, date, changelog_branch) if filing else [],
    )


def changelog_action(state: FernState, date: str | None) -> Action:
    return action(state.changelog_pr) if date else Action.SKIP


def record(
    gh: Gh, tag: str, date: str | None, out: Path, *, rehearsal: bool
) -> FernState:
    state = read_state(gh, tag, date, rehearsal=rehearsal)
    out.mkdir(parents=True, exist_ok=True)
    (out / STATE_FILE).write_text(state.model_dump_json(indent=2), encoding="utf-8")
    (out / DOCS_MERGED_FILE).write_text(docs_listing(gh, PrState.MERGED), "utf-8")
    (out / DOCS_OPEN_FILE).write_text(docs_listing(gh, PrState.OPEN), "utf-8")
    return state


def main(argv: Sequence[str] | None = None, gh: Gh = gh_output) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--date", help="the release date; omit when every item is held")
    parser.add_argument("--rehearsal", action="store_true", help="test mode's branches")
    parser.add_argument("--outputs", type=Path, help="append the part actions here")
    args = parser.parse_args(argv)

    try:
        state = record(gh, args.tag, args.date, args.out, rehearsal=args.rehearsal)
    except ValidationError as exc:
        logger.error(
            "A Fern PR listing does not match its shape:\n%s", shape_errors(exc)
        )
        return 1
    except RuntimeError as exc:
        logger.error("error: %s", exc)
        return 1
    changelog, docs = changelog_action(state, args.date), action(state.docs_pr)
    logger.info("changelog: %s; docs: %s", changelog, docs)
    if state.date_conflicts:
        logger.error(
            "Another open Fern PR changes the date file; merge or close it, then "
            "re-run: %s",
            " ".join(state.date_conflicts),
        )
        return 1
    append_outputs(args.outputs, changelog=changelog, docs=docs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
