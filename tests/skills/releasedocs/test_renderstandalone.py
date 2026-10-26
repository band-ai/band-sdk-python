"""The standalone Fern entry follows the format spec and passes the real validator."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from tests.skills.releasedocs.samples import (
    LEGACY_TOOLS,
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
            product_label="Python SDK",
            product_tag="python",
            release_date=RELEASE_DATE,
        )
        assert violations == []

    def test_breaking_report_renders_the_product_titled_block(
        self,
        renderentry: ModuleType,
        releasedata: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        breaking = releasedata.Breaking(
            title="Platform Tool Names Now Prefixed", markdown=BREAKING_MARKDOWN
        )
        notes = make_notes([make_item(*TOOL_PREFIX)], breaking)
        path = renderentry.write_standalone(notes, tmp_path)
        assert path.read_text(encoding="utf-8") == (
            "---\n"
            'tags: ["improvement", "breaking-change", "python"]\n'
            "---\n"
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

    def test_entry_failing_the_validator_is_not_written(
        self,
        renderentry: ModuleType,
        releasedata: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
        tmp_path: Path,
    ) -> None:
        breaking = releasedata.Breaking(
            title="Renamed", markdown="## Action Required\nRename."
        )
        notes = make_notes([make_item(*TOOL_PREFIX)], breaking)
        notes_file = tmp_path / "release-notes.json"
        notes_file.write_text(notes.model_dump_json(), encoding="utf-8")
        out = tmp_path / "bundle"
        assert renderentry.main([str(notes_file), "--out", str(out)]) == 1
        assert not (out / f"{RELEASE_DATE}.md").exists()
