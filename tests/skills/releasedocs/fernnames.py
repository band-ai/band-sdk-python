"""Kit: the Fern branch and file names the filing scripts derive for the sample release."""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

from tests.skills.releasedocs.samples import PREVIOUS_TAG, RELEASE_DATE, RELEASE_TAG


@dataclass(frozen=True)
class FernNames:
    changelog: str
    docs: str
    changelog_rehearsal: str
    docs_rehearsal: str
    # The open docs PR of the previous release, which the new one supersedes.
    older_docs: str
    date_file: str

    @classmethod
    def derive(cls, releasedata: ModuleType) -> FernNames:
        branch = releasedata.fern_branch
        changelog, docs = (
            releasedata.CHANGELOG_BRANCH_PREFIX,
            releasedata.DOCS_BRANCH_PREFIX,
        )
        return cls(
            changelog=branch(changelog, RELEASE_TAG, rehearsal=False),
            docs=branch(docs, RELEASE_TAG, rehearsal=False),
            changelog_rehearsal=branch(changelog, RELEASE_TAG, rehearsal=True),
            docs_rehearsal=branch(docs, RELEASE_TAG, rehearsal=True),
            older_docs=branch(docs, PREVIOUS_TAG, rehearsal=False),
            date_file=f"{releasedata.CHANGELOG_DIR}/{RELEASE_DATE}.md",
        )
