# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""Data shapes exchanged between the release-notes scripts and the CI model.

``release-prs.json`` (collector -> model), the model's structured report, and
``release-notes.json`` (the bundle every renderer reads) are defined here once.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

SDK_TAG_PREFIX = "band-sdk-v"


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
