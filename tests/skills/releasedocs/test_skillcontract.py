"""The skill's guides delegate the entry format to its one reference and validator."""

from __future__ import annotations

import pytest

from tests.paths import RELEASE_DOCS_SKILL

REFERENCE = "references/fern-changelog-format.md"
GUIDES = ["SKILL.md", "changelog.md"]
# Rules that belong only in the reference; a copy in a guide is how entries drifted.
REFERENCE_ONLY = ['tags: ["', "- **Python SDK:**", "### Migration"]


class TestSkillContract:
    @pytest.mark.parametrize("guide", GUIDES)
    def test_guide_links_the_reference_and_restates_no_rule(self, guide: str) -> None:
        text = (RELEASE_DOCS_SKILL / guide).read_text(encoding="utf-8")
        assert REFERENCE in text
        assert [rule for rule in REFERENCE_ONLY if rule in text] == []

    def test_reference_names_the_validator_that_enforces_it(self) -> None:
        reference = (RELEASE_DOCS_SKILL / REFERENCE).read_text(encoding="utf-8")
        assert "scripts/validateentry.py" in reference
