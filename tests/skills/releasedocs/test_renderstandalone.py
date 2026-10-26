"""The standalone Fern entry follows the format spec and passes the real validator."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from tests.skills.releasedocs.samples import (
    LEGACY_TOOLS,
    PRODUCT,
    RECONNECT,
    RELEASE_DATE,
    SEND_RETRIES,
    TOKEN_REDACTION,
    TOOL_PREFIX,
)

BREAKING_MARKDOWN = """Tools now carry a `band_` prefix.

| Before | After |
|---|---|
| `send` | `band_send` |

### What This Means
Existing agents must update.

### Migration
Rename the tools."""
# Code comments and list markers inside a fence are not headings or bullets.
MIGRATION_CODE = """```python
# Before
send()
# After
band_send()
- not a bullet
```"""


class TestStandaloneEntry:
    def test_categories_sort_and_tags_follow_tag_order(
        self,
        renderentry: ModuleType,
        validateentry: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        notes = make_notes(
            [
                make_item(*RECONNECT),
                make_item(*TOKEN_REDACTION),
                make_item(*SEND_RETRIES),
                make_item(*LEGACY_TOOLS),
            ]
        )
        path = renderentry.write_standalone(notes, tmp_path)
        assert path.name == f"{RELEASE_DATE}.md"
        assert path.read_text(encoding="utf-8") == (
            "---\n"
            'tags: ["feature", "breaking-change", "security", "fix", "python"]\n'
            "---\n"
            "\n"
            "## Python SDK 6.1.0\n"
            "\n"
            "## Added\n"
            "- **Python SDK:**\n"
            "  - **Send Retries** — Sends retry with backoff.\n"
            "\n"
            "## Removed\n"
            "- **Python SDK:**\n"
            "  - **Legacy Tools** — The deprecated tool names are gone.\n"
            "\n"
            "## Fixed\n"
            "- **Python SDK:**\n"
            "  - **Reconnect** — Rooms rejoin after a drop.\n"
            "\n"
            "## Security\n"
            "- **Python SDK:**\n"
            "  - **Token Redaction** — Keys never reach logs.\n"
        )
        violations = validateentry.validate(
            path,
            surface="sdks",
            product_label=PRODUCT["label"],
            product_tag=PRODUCT["tag"],
            release_date=RELEASE_DATE,
            release_version=notes.version,
        )
        assert violations == []

    @pytest.mark.parametrize(
        "title",
        [
            "Platform Tool Names Now Prefixed",
            "Python SDK: Platform Tool Names Now Prefixed",
        ],
        ids=["bare-title", "product-prefixed-title"],
    )
    def test_breaking_report_renders_the_product_titled_block(
        self,
        renderentry: ModuleType,
        releasedata: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
        title: str,
    ) -> None:
        breaking = releasedata.Breaking(title=title, markdown=BREAKING_MARKDOWN)
        notes = make_notes([make_item(*TOOL_PREFIX)], breaking)
        path = renderentry.write_standalone(notes, tmp_path)
        assert path.read_text(encoding="utf-8") == (
            "---\n"
            'tags: ["improvement", "breaking-change", "python"]\n'
            "---\n"
            "\n"
            "## Python SDK 6.1.0\n"
            "\n"
            "## Python SDK: Platform Tool Names Now Prefixed\n"
            "\n"
            f"{BREAKING_MARKDOWN}\n"
            "\n"
            "## Changed\n"
            "- **Python SDK:**\n"
            "  - **Tool Prefix** — Tool names start with a prefix.\n"
        )

    def test_model_text_renders_escaped(
        self,
        renderentry: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        item = make_item(
            "Added",
            "Agent<T> Support",
            "Pass {x} or `capabilities={Capability.MEMORY}`.",
        )
        text = renderentry.write_standalone(make_notes([item]), tmp_path).read_text(
            encoding="utf-8"
        )
        assert text.splitlines()[-1] == (
            r"  - **Agent\<T\> Support** — Pass \{x\} or `capabilities={Capability.MEMORY}`."
        )

    @pytest.mark.parametrize(
        "markdown",
        ["## Action Required\nRename.", "Rename.\n\n```python\nfrom band import Y\n"],
        ids=["validator-violation", "unclosed-fence"],
    )
    def test_rejected_entry_is_not_written(
        self,
        renderentry: ModuleType,
        releasedata: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
        markdown: str,
    ) -> None:
        breaking = releasedata.Breaking(title="Renamed", markdown=markdown)
        notes = make_notes([make_item(*TOOL_PREFIX)], breaking)
        notes_file = tmp_path / "release-notes.json"
        notes_file.write_text(notes.model_dump_json(), encoding="utf-8")
        out = tmp_path / "bundle"
        assert renderentry.main([str(notes_file), "--out", str(out)]) == 1
        assert not (out / f"{RELEASE_DATE}.md").exists()

    def test_migration_code_passes_the_validator_verbatim(
        self,
        renderentry: ModuleType,
        releasedata: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        breaking = releasedata.Breaking(
            title="Tools Renamed",
            markdown=f"{BREAKING_MARKDOWN}\n\n{MIGRATION_CODE}",
        )
        notes = make_notes([make_item(*TOOL_PREFIX)], breaking)
        path = renderentry.write_standalone(notes, tmp_path)
        assert MIGRATION_CODE in path.read_text(encoding="utf-8")
