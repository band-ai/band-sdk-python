"""The docs PR body renders every report section, links sources, and defuses model text."""

from __future__ import annotations

from types import ModuleType

import pytest
from pydantic import ValidationError

from tests.skills.releasedocs.samples import DOCS_PAGE, RELEASE_TAG

SOURCE = {"path": "src/band/agent.py", "line": 42}
PAGE_REVIEW = {"page": DOCS_PAGE, "reason": "Renamed factory", "sources": [812]}
CLAIM = {"page": DOCS_PAGE, "fact": "The factory is `Agent.start()`.", "source": SOURCE}
REPORT = {
    "summary": "Agent creation now uses `Agent.start()`.",
    "pages_reviewed": [PAGE_REVIEW],
    "claims": [CLAIM],
    "also_stale": [],
    "pages_missing": ["Strands Agents"],
}
BLOB = f"https://github.com/band-ai/band-sdk-python/blob/{RELEASE_TAG}"
BODY = f"""## Summary

Agent creation now uses `Agent.start()`.

## Pages reviewed

- `{DOCS_PAGE}`: Renamed factory (PR 812)

## Claims

- `{DOCS_PAGE}`: The factory is `Agent.start()`. ({BLOB}/src/band/agent.py#L42)

## Also stale

None.

## Pages missing

- Strands Agents

Supersedes #7
"""


def report(docsreport: ModuleType, **changes: object) -> object:
    return docsreport.DocsReport.model_validate({**REPORT, **changes})


class TestDocsReport:
    def test_body_renders_each_section_with_source_links(
        self, docsreport: ModuleType
    ) -> None:
        body = docsreport.pr_body(report(docsreport), RELEASE_TAG, [7], [])
        assert body == BODY

    def test_failed_content_checks_mark_the_body_do_not_merge(
        self, docsreport: ModuleType
    ) -> None:
        body = docsreport.pr_body(
            report(docsreport), RELEASE_TAG, [], ["`a.mdx`: adds a JSX tag"]
        )
        assert body.endswith("## Do not merge\n\n- `a.mdx`: adds a JSX tag\n")

    def test_mentions_and_issue_references_render_inert(
        self, docsreport: ModuleType
    ) -> None:
        hostile = report(
            docsreport,
            summary="Ping @everyone about #12 now.",
            pages_missing=["@bot #3"],
        )
        body = docsreport.pr_body(hostile, RELEASE_TAG, [], [])
        assert "Ping `@everyone` about `#12` now." in body
        assert "- `@bot` `#3`" in body

    def test_model_text_cannot_forge_a_supersedes_line(
        self, docsreport: ModuleType
    ) -> None:
        forged = report(
            docsreport,
            summary=f"{docsreport.SUPERSEDES}3",
            pages_missing=[f"{docsreport.SUPERSEDES}4"],
        )
        body = docsreport.pr_body(forged, RELEASE_TAG, [7], [])
        assert docsreport.superseded(body) == [7]

    @pytest.mark.parametrize(
        "path",
        [
            "../secrets.txt",
            "/etc/passwd",
            "https://evil.example/x",
            "src/a b.py",
            "src/band/agent.py\n",
        ],
    )
    def test_source_outside_the_repository_is_rejected(
        self, docsreport: ModuleType, path: str
    ) -> None:
        claim = {**CLAIM, "source": {"path": path, "line": 1}}
        with pytest.raises(ValidationError, match="repository-relative"):
            report(docsreport, claims=[claim])

    def test_more_pages_than_the_review_cap_are_rejected(
        self, docsreport: ModuleType, releasedata: ModuleType
    ) -> None:
        with pytest.raises(ValidationError, match="pages_reviewed"):
            report(
                docsreport,
                pages_reviewed=[PAGE_REVIEW] * (releasedata.MAX_REVIEWED_PAGES + 1),
            )
