"""The vendored validator enforces the Fern changelog format reference."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

# Trimmed from thenvoi/fern fern/changelog/sdks/2026-08-03.md: the shape every entry matches.
CANONICAL_SDK_ENTRY = """---
tags: ["feature", "improvement", "fix", "python"]
---

## Added
- **Python SDK:**
  - **Strands Agents Adapter** — Connect an AWS Strands Agents-based agent to Band chat rooms.

## Changed
- **Python SDK:**
  - **OpenCode Adapter Reaches Full Conformance** — The OpenCode adapter now meets the L0-L4 bar.

## Fixed
- **Python SDK:**
  - **Parlant Adapter Port Collisions** — Two Parlant agents on one machine no longer crash on port binding.
"""

# Trimmed from thenvoi/fern#344: the drift the validator exists to catch.
DRIFTED_SDK_ENTRY = """---
tags: ["fix", "typescript"]
---

<Badge intent="info">fix</Badge> <Badge>typescript</Badge>

## Fixed
### TypeScript SDK
- **LangGraph Adapter Reliability** — Fixes agents going silent across turns.
"""
PYTHON = {"product_label": "Python SDK", "product_tag": "python"}
SWAPPED = """## Fixed
- **Python SDK:**
  - **A** — One.

## Added
- **Python SDK:**
  - **B** — Two.
"""


@pytest.fixture
def check(validateentry: ModuleType, tmp_path: Path) -> Callable[..., list[str]]:
    """Validate ``text`` written as ``name`` (sdks surface unless overridden)."""

    def run(text: str, name: str = "2026-08-03.md", **kwargs: object) -> list[str]:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8", newline="")
        kwargs.setdefault("surface", "sdks")
        return validateentry.validate(path, **kwargs)

    return run


def mentions(violations: list[str], needle: str) -> bool:
    return any(needle in violation for violation in violations)


class TestValidateEntry:
    def test_canonical_sdk_entry_passes(self, check: Callable[..., list[str]]) -> None:
        assert check(CANONICAL_SDK_ENTRY, release_date="2026-08-03", **PYTHON) == []

    @pytest.mark.parametrize(
        "needle",
        ["Badge components were removed", "### TypeScript SDK", "**TypeScript SDK:**"],
    )
    def test_drifted_entry_is_reported(
        self, check: Callable[..., list[str]], needle: str
    ) -> None:
        violations = check(
            DRIFTED_SDK_ENTRY,
            name="2026-08-09.md",
            product_label="TypeScript SDK",
            product_tag="typescript",
            release_date="2026-08-09",
        )
        assert mentions(violations, needle)

    @pytest.mark.parametrize(
        ("text", "kwargs", "needle"),
        [
            (
                CANONICAL_SDK_ENTRY + "\n## Action Required\n- Update your pins.\n",
                PYTHON,
                "'Action Required' is a '### ' subsection",
            ),
            (
                CANONICAL_SDK_ENTRY.replace(
                    "## Added", "## Shiny New Thing\n\n## Added", 1
                ),
                PYTHON,
                "is not a Keep a Changelog category",
            ),
            (SWAPPED, {"surface": "platform"}, "must follow Keep a Changelog order"),
            (
                SWAPPED.replace("## Added", "## Fixed", 1),
                {"surface": "platform"},
                "duplicate section '## Fixed'",
            ),
            (
                CANONICAL_SDK_ENTRY.replace(
                    '"feature", "improvement", "fix"', '"feature", "improvement"', 1
                ),
                PYTHON,
                "section '## Fixed' requires the 'fix' tag",
            ),
            (
                CANONICAL_SDK_ENTRY.replace(
                    '["feature", "improvement", "fix", "python"]',
                    '["fix", "feature", "improvement", "python"]',
                    1,
                ),
                PYTHON,
                "canonical order",
            ),
            (
                CANONICAL_SDK_ENTRY.replace(
                    '["feature", "improvement", "fix", "python"]',
                    '["python", "feature", "improvement", "fix"]',
                    1,
                ),
                PYTHON,
                "must come last",
            ),
            (
                CANONICAL_SDK_ENTRY.replace('"feature"', '"shiny"', 1),
                PYTHON,
                "unknown tag 'shiny'",
            ),
            (
                CANONICAL_SDK_ENTRY,
                {"surface": "platform"},
                "the platform surface carries no frontmatter",
            ),
            (
                CANONICAL_SDK_ENTRY.split("---\n", 2)[2].lstrip("\n"),
                {},
                "sdks entries require a frontmatter tags block",
            ),
            (
                CANONICAL_SDK_ENTRY.replace("## Added\n- ", "## Added\n\n- ", 1),
                PYTHON,
                "no blank line between a category heading and its first bullet",
            ),
            (
                CANONICAL_SDK_ENTRY.replace(
                    "**Strands Agents Adapter** —", "**Strands Agents Adapter** -", 1
                ),
                PYTHON,
                "em dash",
            ),
            (
                (CANONICAL_SDK_ENTRY + "\n## Security\n").replace(
                    '"fix"', '"security", "fix"', 1
                ),
                PYTHON,
                "section has no bullets",
            ),
        ],
        ids=[
            "action-required-heading",
            "entry-title-without-breaking",
            "category-order",
            "duplicate-category",
            "missing-section-tag",
            "tag-order",
            "product-tag-not-last",
            "unknown-tag",
            "frontmatter-on-platform",
            "sdks-without-frontmatter",
            "blank-line-after-heading",
            "hyphen-separator",
            "empty-section",
        ],
    )
    def test_format_violation_is_reported(
        self,
        check: Callable[..., list[str]],
        text: str,
        kwargs: dict[str, object],
        needle: str,
    ) -> None:
        assert mentions(check(text, **kwargs), needle)

    @pytest.mark.parametrize(
        ("text", "kwargs"),
        [
            (
                CANONICAL_SDK_ENTRY.replace(
                    '"improvement"', '"improvement", "breaking-change"', 1
                ).replace(
                    "## Added", "## Python SDK: Tool Names Now Prefixed\n\n## Added", 1
                ),
                PYTHON | {"breaking": True},
            ),
            (
                CANONICAL_SDK_ENTRY.replace('"improvement"', '"breaking-change"', 1),
                PYTHON,
            ),
            (
                CANONICAL_SDK_ENTRY.split("---\n", 2)[2].lstrip("\n"),
                {"surface": "rest-api"},
            ),
        ],
        ids=["breaking-entry-title", "changed-tagged-breaking", "untagged-surface"],
    )
    def test_accepted_variant_passes(
        self, check: Callable[..., list[str]], text: str, kwargs: dict[str, object]
    ) -> None:
        assert check(text, **kwargs) == []

    @pytest.mark.parametrize(
        ("name", "kwargs", "needle"),
        [
            ("typescript-0.1.10.md", {}, "filename must be YYYY-MM-DD.md"),
            (
                "2026-08-10.md",
                {"release_date": "2026-08-03"},
                "must use the release date 2026-08-03.md",
            ),
        ],
    )
    def test_filename_must_be_the_release_date(
        self,
        check: Callable[..., list[str]],
        name: str,
        kwargs: dict[str, object],
        needle: str,
    ) -> None:
        assert mentions(
            check(CANONICAL_SDK_ENTRY, name=name, surface="platform", **kwargs), needle
        )

    @pytest.mark.parametrize(
        ("text", "args", "code"),
        [
            (
                CANONICAL_SDK_ENTRY,
                ["--product-label", "Python SDK", "--product-tag", "python"],
                0,
            ),
            (DRIFTED_SDK_ENTRY, [], 1),
        ],
        ids=["clean", "violations"],
    )
    def test_cli_exit_code_reports_violations(
        self,
        validateentry: ModuleType,
        tmp_path: Path,
        text: str,
        args: list[str],
        code: int,
    ) -> None:
        path = tmp_path / "2026-08-03.md"
        path.write_text(text, encoding="utf-8", newline="")
        assert validateentry.main([str(path), "--surface", "sdks", *args]) == code

    def test_cli_missing_entry_is_a_usage_error(
        self, validateentry: ModuleType, tmp_path: Path
    ) -> None:
        assert (
            validateentry.main([str(tmp_path / "missing.md"), "--surface", "sdks"]) == 2
        )
