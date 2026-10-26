"""Model text is checked once and escaped for each destination it reaches."""

from __future__ import annotations

from types import ModuleType

import pytest


class TestTextSafety:
    @pytest.mark.parametrize(
        ("text", "findings"),
        [
            ("Uses sk-ant-abc to call", ["sk-ant-"]),
            ("Token ghp_x and ghs_y", ["ghp_", "ghs_"]),
            ("See https://example.com/docs.", ["https://example.com/docs."]),
            ("Visit www.example.com", ["www.example.com"]),
            ("Posts to hooks.slack.com/services/T/B/x", ["hooks.slack.com/"]),
            ("Retries now back off between attempts.", []),
        ],
    )
    def test_unsafe_findings_name_credentials_and_urls(
        self, textsafety: ModuleType, text: str, findings: list[str]
    ) -> None:
        assert textsafety.unsafe_findings(text) == findings

    def test_slack_shows_control_syntax_literally(self, textsafety: ModuleType) -> None:
        assert textsafety.slack_text("<!channel> & <https://x|y>") == (
            "&lt;!channel&gt; &amp; &lt;https://x|y&gt;"
        )

    def test_discord_items_cannot_ping_the_channel(
        self, textsafety: ModuleType
    ) -> None:
        assert textsafety.discord_text("Ping @here and @everyone now") == (
            "Ping  and  now"
        )

    @pytest.mark.parametrize(
        ("text", "escaped"),
        [
            ("Pass {x} to <Tag>", r"Pass \{x\} to \<Tag\>"),
            (
                "Set `capabilities={Capability.MEMORY}` on <Agent>",
                r"Set `capabilities={Capability.MEMORY}` on \<Agent\>",
            ),
            ("An unbalanced ` then {x}", r"An unbalanced ` then \{x\}"),
            (
                "```python\nx = {1}\n```\nthen {y}",
                "```python\nx = {1}\n```\nthen \\{y\\}",
            ),
            ("Retries back off.", "Retries back off."),
        ],
    )
    def test_mdx_escapes_outside_code_only(
        self, textsafety: ModuleType, text: str, escaped: str
    ) -> None:
        assert textsafety.mdx_text(text) == escaped

    @pytest.mark.parametrize(
        ("text", "inert"),
        [
            ("Thanks @team", "Thanks `@team`"),
            ("Closes #12", "Closes `#12`"),
            (
                "Closes https://github.com/o/r/issues/12",
                "Closes `https://github.com/o/r/issues/12`",
            ),
            ("Keep `@decorator` as is", "Keep `@decorator` as is"),
            ("Mail ops@example.com", "Mail ops@example.com"),
            ("Retries back off.", "Retries back off."),
        ],
    )
    def test_pr_bodies_neither_mention_nor_link(
        self, textsafety: ModuleType, text: str, inert: str
    ) -> None:
        assert textsafety.pr_body_text(text) == inert
