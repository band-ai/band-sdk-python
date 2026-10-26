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
            ("Token github_pat_x", ["github_pat_"]),
            ("Posts to hooks.slack.com/services/T/B/x", ["hooks.slack.com/"]),
            ("Posts to discord.com/api/webhooks/1/x", ["discord.com/api/webhooks/"]),
            (
                "Posts to discordapp.com/api/webhooks/1/x",
                ["discordapp.com/api/webhooks/"],
            ),
            ("Retries now back off between attempts.", []),
        ],
    )
    def test_unsafe_findings_name_credentials(
        self, textsafety: ModuleType, text: str, findings: list[str]
    ) -> None:
        assert textsafety.unsafe_findings(text) == findings

    @pytest.mark.parametrize(
        "text", ["See https://example.com/docs.", "Visit www.example.com"]
    )
    def test_unsafe_findings_name_a_url_without_its_text(
        self, textsafety: ModuleType, text: str
    ) -> None:
        assert textsafety.unsafe_findings(text) == [textsafety.URL_FINDING]

    def test_leaked_webhook_is_named_but_never_repeated(
        self, textsafety: ModuleType
    ) -> None:
        secret = "https://hooks.slack.com/services/T0/B0/s3cr3tT0k3n"
        findings = textsafety.unsafe_findings(f"Posts to {secret}")
        assert findings == ["hooks.slack.com/", textsafety.URL_FINDING]
        assert "s3cr3tT0k3n" not in " ".join(findings)

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
            (
                "~~~python\nx = {1}\n~~~\nthen {y}",
                "~~~python\nx = {1}\n~~~\nthen \\{y\\}",
            ),
            ("Use `x then {a}.\n\nLater `y`", "Use `x then \\{a\\}.\n\nLater `y`"),
            ("`a\nb {x}` and {y}", "`a\nb {x}` and \\{y\\}"),
            ("```\n~~~\n{a}\n```", "```\n~~~\n{a}\n```"),
            (
                "1. Update:\n\n   ```python\n   cfg = {1}\n\n   x = <2>\n   ```",
                "1. Update:\n\n   ```python\n   cfg = {1}\n\n   x = <2>\n   ```",
            ),
            ("```\n{a}\n````\nthen {y}", "```\n{a}\n````\nthen \\{y\\}"),
            (
                "Use `old or\n- set {x} now\nthen` done.",
                "Use `old or\n- set \\{x\\} now\nthen` done.",
            ),
            ("Use C:\\{name} now", "Use C:\\\\\\{name\\} now"),
            ("Retries back off.", "Retries back off."),
        ],
    )
    def test_mdx_escapes_outside_code_only(
        self, textsafety: ModuleType, text: str, escaped: str
    ) -> None:
        assert textsafety.mdx_text(text) == escaped

    @pytest.mark.parametrize(
        ("text", "unclosed"),
        [
            ("```python\nx = 1\n", True),
            ("```python\nx = 1\n~~~\n", True),
            ("```a`b\n\n{x}\n\n```\n", True),
            ("```python\nx = 1\n```\n", False),
            ("```\nx = 1\n````\n", False),
            ("1. Update:\n\n   ```python\n   x = 1\n   ```\n", False),
        ],
        ids=[
            "never-closed",
            "closed-by-the-other-character",
            "backtick-in-the-info-string",
            "closed",
            "closed-by-a-longer-run",
            "indented-in-a-list",
        ],
    )
    def test_unclosed_fence_is_detected(
        self, textsafety: ModuleType, text: str, unclosed: bool
    ) -> None:
        assert textsafety.has_unclosed_fence(text) is unclosed

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
