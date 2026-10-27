# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""The docs review's structured report, and the Fern docs PR body rendered from it.

Every model-written string reaches the body through ``pr_body_text``; source
links are built only from a validated repository path and line.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Annotated

from pydantic import AfterValidator, Field
from releasedata import MAX_REVIEWED_PAGES, SDK_REPO_URL, Frozen, single_line
from textsafety import or_none, pr_body_text

# A repository-relative path: no scheme, no parent step, no leading slash.
SOURCE_PATH = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[\w.@+/-]+$")
# The section listing the content checks that keep a docs PR from merging.
DO_NOT_MERGE = "Do not merge"
SUPERSEDES = "Supersedes #"
# Model text cannot forge this line: pr_body_text quotes every `#N` it writes.
SUPERSEDES_LINE = re.compile(rf"^{SUPERSEDES}(\d+)$", re.MULTILINE)

Line = Annotated[str, Field(min_length=1), AfterValidator(single_line)]


def source_path(path: str) -> str:
    if not SOURCE_PATH.fullmatch(path):
        raise ValueError("must be a repository-relative path")
    return path


class SourceRef(Frozen):
    path: Annotated[
        str,
        Field(description="Repository-relative path in sdk/, e.g. src/band/agent.py"),
        AfterValidator(source_path),
    ]
    line: Annotated[int, Field(ge=1)]


class PageReview(Frozen):
    page: Line
    reason: Line
    sources: list[int] = Field(description="PR numbers from review-prs.json")


class Claim(Frozen):
    page: Line
    fact: Annotated[Line, Field(description="The changed fact, one line")]
    source: SourceRef


class Drift(Frozen):
    page: Line
    issue: Annotated[Line, Field(description="Staleness that predates the range")]
    source: SourceRef


class DocsReport(Frozen):
    """The structured output the docs review returns."""

    summary: Line
    pages_reviewed: list[PageReview] = Field(max_length=MAX_REVIEWED_PAGES)
    claims: list[Claim]
    also_stale: list[Drift]
    pages_missing: list[Annotated[str, AfterValidator(single_line)]] = Field(
        description="Framework adapters at the tag that no tutorial page documents"
    )


def source_link(tag: str, source: SourceRef) -> str:
    return f"{SDK_REPO_URL}/blob/{tag}/{source.path}#L{source.line}"


def bullets(lines: Sequence[str]) -> list[str]:
    return or_none([f"- {pr_body_text(line)}" for line in lines])


def heading(title: str) -> str:
    return f"## {title}"


def section(title: str, lines: list[str]) -> list[str]:
    return [heading(title), "", *lines, ""]


def review_line(page: PageReview) -> str:
    sources = ", ".join(f"PR {number}" for number in page.sources) or "no PR"
    return f"`{page.page}`: {page.reason} ({sources})"


def claim_line(tag: str, page: str, text: str, source: SourceRef) -> str:
    return f"- {pr_body_text(f'`{page}`: {text}')} ({source_link(tag, source)})"


def pr_body(
    report: DocsReport,
    tag: str,
    supersede: Sequence[int],
    do_not_merge: Sequence[str],
) -> str:
    """Render the docs PR body; ``do_not_merge`` lists the failed content checks."""
    blocks = [
        section("Summary", [pr_body_text(report.summary)]),
        section(
            "Pages reviewed", bullets(list(map(review_line, report.pages_reviewed)))
        ),
        section(
            "Claims",
            or_none([claim_line(tag, c.page, c.fact, c.source) for c in report.claims]),
        ),
        section(
            "Also stale",
            or_none(
                [claim_line(tag, d.page, d.issue, d.source) for d in report.also_stale]
            ),
        ),
        section("Pages missing", bullets(report.pages_missing)),
    ]
    if supersede:
        blocks.append([*(f"{SUPERSEDES}{number}" for number in supersede), ""])
    if do_not_merge:
        blocks.append(section(DO_NOT_MERGE, bullets(do_not_merge)))
    return "\n".join(line for block in blocks for line in block)


def superseded(body: str) -> list[int]:
    """The PRs a docs PR body, as ``pr_body`` rendered it, supersedes."""
    return [int(number) for number in SUPERSEDES_LINE.findall(body)]
