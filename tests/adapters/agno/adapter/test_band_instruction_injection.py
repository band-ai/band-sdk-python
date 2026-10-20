from __future__ import annotations

import pytest

from band.core.types import Capability


class TestBandInstructionInjection:
    """Drive a real Agno agent so we assert on the system prompt Agno actually
    assembled and sent to the model, not the attribute the adapter set."""

    @pytest.mark.parametrize(
        ("capabilities", "present", "absent"),
        [
            (set(), [], ["## Memory Tools", "## Contact Management Tools"]),
            (
                {Capability.MEMORY},
                ["## Memory Tools"],
                ["## Contact Management Tools"],
            ),
            (
                {Capability.CONTACTS},
                ["## Contact Management Tools"],
                ["## Memory Tools"],
            ),
        ],
    )
    async def test_capability_sections_gated_in_model_prompt(
        self, run_real_agent, sample_platform_message, capabilities, present, absent
    ):
        model = await run_real_agent(
            sample_platform_message,
            capabilities=capabilities,
        )
        prompt = model.captured_system_prompt

        assert "## Environment" in prompt  # base guidance always injected
        assert all(section in prompt for section in present)
        assert all(section not in prompt for section in absent)

    async def test_developer_instructions_survive_in_prompt(
        self, run_real_agent, sample_platform_message
    ):
        model = await run_real_agent(
            sample_platform_message,
            instructions="You are Dev, a niche specialist.",
            additional_context="Keep replies under 10 words.",
        )
        prompt = model.captured_system_prompt

        assert "You are Dev, a niche specialist." in prompt
        assert "Keep replies under 10 words." in prompt
        assert "## Environment" in prompt

    async def test_guidance_injected_at_startup_before_any_message(
        self, make_started_adapter
    ):
        # Band guidance is injected in on_started, not lazily on first message.
        _adapter, agent = await make_started_adapter()

        # The Band operating contract frames the agent via ``description`` (ahead of
        # the developer's own ``instructions``), not via ``additional_context``.
        assert isinstance(agent.description, str)
        assert "## Environment" in agent.description
        # Band-registered identity is injected so the model knows who it is.
        assert "You are TestBot, desc." in agent.description
