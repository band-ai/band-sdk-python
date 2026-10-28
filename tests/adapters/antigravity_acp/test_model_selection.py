"""Apply model and thinking choices on fresh and restored native sessions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from band.integrations.acp.model_selection import MODEL_CATEGORY, THOUGHT_LEVEL_CATEGORY
from band.integrations.acp.session_config import CONFIG_FAILURE_PREFIX
from band.integrations.acp.types import ACP_FAILURE_PROVIDER
from band.testing.fake_tools import failure_reports
from tests.adapters.antigravity_acp.kit import AntigravityRoom
from tests.adapters.antigravity_acp.peerconfig import PeerEvent, PeerSettings
from tests.integrations.acp.acp_toolkit.agent import select_option


class TestAntigravityModelSelection:
    async def test_an_unknown_model_is_reported_without_prompting_the_provider(
        self, open_peer: Callable[..., Awaitable[AntigravityRoom]]
    ) -> None:
        peer = await open_peer(
            PeerSettings(
                config_options=[
                    select_option(
                        "model", "model-one", ["model-one"], category=MODEL_CATEGORY
                    )
                ]
            ),
            config={"model": "unsupported"},
        )

        tools = await peer.send()

        assert failure_reports(tools) == [
            (
                ACP_FAILURE_PROVIDER,
                f'{CONFIG_FAILURE_PREFIX}model "unsupported" is not advertised; available: model-one',
            )
        ]
        assert peer.records(PeerEvent.PROMPTED) == []

    async def test_model_and_thinking_are_reapplied_after_restore(
        self, open_peer: Callable[..., Awaitable[AntigravityRoom]]
    ) -> None:
        settings = PeerSettings(
            config_options=[
                select_option(
                    "model",
                    "model-one",
                    ["model-one", "model-two"],
                    category=MODEL_CATEGORY,
                ),
                select_option(
                    "thinking_level",
                    "low",
                    ["low", "high"],
                    category=THOUGHT_LEVEL_CATEGORY,
                ),
            ]
        )
        config = {"model": "model-two", "reasoning_effort": "high"}
        first = await open_peer(settings, config=config)
        tools = await first.send()
        await first.adapter.cleanup_all()
        second = await open_peer(
            settings, config={**config, "gemini_home": first.adapter.config.gemini_home}
        )

        await second.send(history=tools.events_sent)

        expected = [
            {"event": PeerEvent.CONFIGURED, "option": "model", "value": "model-two"},
            {
                "event": PeerEvent.CONFIGURED,
                "option": "thinking_level",
                "value": "high",
            },
        ]
        assert first.records(PeerEvent.CONFIGURED) == expected
        assert second.records(PeerEvent.CONFIGURED) == expected
