from __future__ import annotations

import pytest

pytest.importorskip("strands", reason="strands extra not installed")


from band.adapters.strands import (
    StrandsAdapter,
    StrandsAdapterConfig,
)


class TestPromptConfiguration:
    @pytest.mark.asyncio
    async def test_explicit_system_prompt_overrides_rendered_prompt(self):
        adapter = StrandsAdapter(
            StrandsAdapterConfig(
                model="m",
                system_prompt="Use only the requested tools.",
                custom_section="This must not be appended.",
            )
        )

        await adapter.on_started("Bot", "A bot")

        assert adapter._system_prompt == "Use only the requested tools."

    @pytest.mark.asyncio
    async def test_custom_section_is_included_in_rendered_prompt(self):
        adapter = StrandsAdapter(
            StrandsAdapterConfig(model="m", custom_section="Keep replies concise.")
        )

        await adapter.on_started("Bot", "A bot")

        assert adapter._system_prompt is not None
        assert "Keep replies concise." in adapter._system_prompt
