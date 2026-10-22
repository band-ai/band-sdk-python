"""AuthoringDocs specification regression checks."""

from __future__ import annotations

import re
import unittest

from tests.contracts.spec.docclaims.kit import ROOT, SKILL, docs, prose


class TestAuthoringDocs(unittest.TestCase):
    """DF-14: authoring docs teach the 0.2.0 forms and the helper's real default."""

    def test_lab_readme_teaches_no_removed_forms(self) -> None:
        text = (ROOT / "contract-lab/README.md").read_text(encoding="utf-8")
        sections = re.split(r"^(?=#+ )", text, flags=re.MULTILINE)
        removed = re.compile(r"\+TYPE|\+STATE|\bDUE\b.*\bIN\b|0\.1\.0-lab\.1")
        hits = [
            line
            for section in sections
            if not re.match(r"#+ .*legacy", section, re.IGNORECASE)
            for line in section.splitlines()
            if removed.search(line)
        ]
        self.assertEqual(hits, [])

    def test_helper_default_format_matches_the_cli(self) -> None:
        cli = (ROOT / "contract-lab/lib/contract_lab/cli.ex").read_text(
            encoding="utf-8"
        )
        match = re.search(r'Keyword\.get\(opts, :format, "(\w+)"\)', cli)
        assert match is not None, "The engine CLI default format declaration is missing"
        default = match.group(1)
        self.assertEqual(default, "frontmatter")
        stale = re.compile(
            r"retained default parser|defaults to a parser for removed native headers",
            re.IGNORECASE,
        )
        for path in (SKILL / "SKILL.md", SKILL / "references/authoring.md"):
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertIsNone(stale.search(prose(path)))
                self.assertRegex(prose(path), r"helper reads (YAML )?frontmatter")

    def test_readmes_name_no_author_checkout(self) -> None:
        checkout = re.compile(r"/home/[^/\s]+/|~/tv/")
        hits = [
            f"{p.relative_to(ROOT)}:{n}"
            for p in docs("*/README.md", "skills/**/*.md")
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if checkout.search(line) and not p.is_relative_to(ROOT / "investigations")
        ]
        self.assertEqual(
            hits,
            [],
            "use repo-relative paths; investigations/ records external snapshots",
        )

    def test_skill_placeholders_are_not_removed_uppercase_keywords(self) -> None:
        """DF-27 Nit: an uppercase placeholder such as EVENT reads as the removed keyword alias."""
        upper = re.compile(
            r"(?<![A-Za-z0-9_+~])(DUE|OF|WITH|IN|AS|EACH|EVENT|MAX_ENTRIES|ACTIVITY)(?![A-Za-z0-9_])"
        )
        hits = [
            f"{p.relative_to(ROOT)}:{n}"
            for p in docs("skills/**/*.md")
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if upper.search(line)
        ]
        self.assertEqual(
            hits, [], "name placeholders with a non-keyword word, e.g. SIGNAL"
        )
