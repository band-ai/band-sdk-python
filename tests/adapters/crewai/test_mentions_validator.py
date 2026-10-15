from __future__ import annotations

import pytest


class TestMentionsValidator:
    """Models driving CrewAI emit mentions in several shapes; all reach list[str]."""

    @pytest.fixture
    async def send_message_schema(self, CrewAIAdapter, crewai_mocks):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        return next(t for t in tools if t.name == "band_send_message").args_schema

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (["Alice", "Bob"], ["Alice", "Bob"]),
            ('["Alice"]', ["Alice"]),
            # Neither JSON nor a list — what gpt-4o-mini actually emitted.
            ("[@yael.avioz/test2]", ["@yael.avioz/test2"]),
            (None, []),
            ("", []),
        ],
    )
    @pytest.mark.asyncio
    async def test_mentions_normalize_to_list(self, send_message_schema, raw, expected):
        instance = send_message_schema(content="Hello!", mentions=raw)

        assert instance.mentions == expected
