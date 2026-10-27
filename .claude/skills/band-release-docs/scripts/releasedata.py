# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""Data shapes exchanged between the release-notes scripts and the CI model.

``release-prs.json`` (collector -> model), the model's structured reports, and
``release-notes.json`` (the bundle every renderer reads) are defined here once,
with the Fern repository facts every filing script shares.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

SDK_TAG_PREFIX = "band-sdk-v"
SDK_REPO_NAME = "band-sdk-python"
SDK_REPO_URL = f"https://github.com/band-ai/{SDK_REPO_NAME}"
# The repository's name before its rename; its PR numbers carried over.
FORMER_SDK_REPO_NAME = "thenvoi-sdk-python"

FERN_REPO = "thenvoi/fern"
FERN_BASE = "dev"
CHANGELOG_BRANCH_PREFIX = "changelog/python-sdk-"
DOCS_BRANCH_PREFIX = "docs/python-sdk-"
# Test mode files on these twins, so a rehearsal never touches the real branches.
REHEARSAL_SUFFIX = "-rehearsal"
# Paths inside the Fern repository.
CHANGELOG_DIR = "fern/changelog/sdks"
HELD_ITEMS = ".changelog/held-items.yml"
DOCS_STRUCTURE = "docs-structure-summary.md"
# The only files a docs review may change: existing pages under this directory.
DOCS_PAGES_DIR = "fern/docs/pages/integrations/sdks"
DOCS_PAGE_SUFFIX = ".mdx"
MAX_REVIEWED_PAGES = 10


def fern_branch(prefix: str, tag: str, *, rehearsal: bool) -> str:
    return f"{prefix}{tag}{REHEARSAL_SUFFIX if rehearsal else ''}"


def is_docs_page(path: str) -> bool:
    """Whether ``path`` (relative to the Fern root, ``/``-separated) is a docs page."""
    parts = path.split("/")
    return (
        path.startswith(f"{DOCS_PAGES_DIR}/")
        and path.endswith(DOCS_PAGE_SUFFIX)
        and ".." not in parts
        and "" not in parts
    )


class Category(StrEnum):
    """Keep a Changelog categories, in the order an entry lists them."""

    ADDED = "Added"
    CHANGED = "Changed"
    DEPRECATED = "Deprecated"
    REMOVED = "Removed"
    FIXED = "Fixed"
    SECURITY = "Security"


class ReportStatus(StrEnum):
    DOCUMENTED = "documented"
    NO_USER_FACING_CHANGES = "no_user_facing_changes"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PullRequest(Frozen):
    number: int
    sha: str
    title: str
    body: str
    labels: list[str]
    files: list[str]
    type: str | None
    breaking: bool


class LooseCommit(Frozen):
    """A commit in the range that names no PR."""

    sha: str
    subject: str
    files: list[str]
    breaking: bool


class ReleasePrs(Frozen):
    """Everything merged in one band-sdk release range."""

    tag: str
    previous_tag: str
    release_url: str
    release_date: str
    pull_requests: list[PullRequest]
    loose_commits: list[LooseCommit]
    excluded_internal: list[int]

    @property
    def has_changes(self) -> bool:
        return bool(self.pull_requests or self.loose_commits)

    @property
    def sources(self) -> set[str]:
        return {str(pr.number) for pr in self.pull_requests} | {
            commit.sha for commit in self.loose_commits
        }

    @property
    def breaking_sources(self) -> list[str]:
        prs = [str(pr.number) for pr in self.pull_requests if pr.breaking]
        return prs + [commit.sha for commit in self.loose_commits if commit.breaking]


def single_line(text: str) -> str:
    """Reject a line break, which would split the rendered list item or heading."""
    if "\n" in text or "\r" in text:
        raise ValueError("must be a single line")
    return text


class Item(Frozen):
    category: Category
    label: Annotated[
        str,
        Field(min_length=1, description="Noun phrase naming the change, one line"),
        AfterValidator(single_line),
    ]
    sentence: Annotated[
        str,
        Field(min_length=1, description="The user-facing effect, one sentence"),
        AfterValidator(single_line),
    ]
    sources: list[str] = Field(
        description="PR numbers (digits only) or loose commit SHAs from release-prs.json"
    )


def in_categories(items: Sequence[Item], categories: set[Category]) -> list[Item]:
    return [item for item in items if item.category in categories]


class Breaking(Frozen):
    title: Annotated[str, Field(min_length=1), AfterValidator(single_line)]
    markdown: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "Summary line, before/after table, ### What This Means, ### Migration"
            ),
        ),
    ]


class ChangelogReport(Frozen):
    """The structured output the CI model returns."""

    status: ReportStatus
    items: list[Item]
    breaking: Breaking | None

    @model_validator(mode="after")
    def items_match_status(self) -> Self:
        documented = self.status is ReportStatus.DOCUMENTED
        if documented != bool(self.items):
            raise ValueError(
                "a documented report lists items; no_user_facing_changes lists none"
            )
        if not documented and self.breaking is not None:
            raise ValueError("no_user_facing_changes carries no breaking block")
        return self


class HoldbacksReport(Frozen):
    """The structured output the holdbacks review returns."""

    held: list[Annotated[int, Field(ge=0)]] = Field(
        description="Indexes (from 0) of release-notes.json items to hold back"
    )
    hold_breaking: bool = Field(
        description="Whether the breaking block describes a held feature"
    )


class Product(Frozen):
    name: str
    label: str
    tag: str


class ReleaseNotes(Frozen):
    """The validated bundle the renderers and announcers read."""

    tag: str
    previous_tag: str
    release_url: str
    release_date: str
    product: Product
    items: list[Item]
    breaking: Breaking | None

    @property
    def version(self) -> str:
        return self.tag.removeprefix(SDK_TAG_PREFIX)

    def items_in(self, categories: set[Category]) -> list[Item]:
        return in_categories(self.items, categories)
