"""InstructionPolicy composition and canonical instruction resolution."""

from __future__ import annotations

from band.core.instructions import (
    Instruction,
    InstructionMode,
    InstructionPolicy,
    normalize_instructions,
)
from band.core.types import AdapterFeatures, Capability
from band.runtime.prompts import render_system_prompt


def test_normalize_bare_str_is_append() -> None:
    inst = normalize_instructions("hello")
    assert inst is not None
    assert inst.mode is InstructionMode.APPEND
    assert inst.text == "hello"
    assert normalize_instructions("  ") is None


def test_replace_drops_the_base_instructions() -> None:
    """``REPLACE`` is the legacy ``system_prompt=``: the agent's text, alone."""
    rendered = InstructionPolicy().render(
        agent_name="Bot",
        agent_description="desc",
        instructions=Instruction(text="ONLY", mode=InstructionMode.REPLACE),
    )
    assert rendered == "ONLY"


def test_append_adds_developer_section() -> None:
    rendered = InstructionPolicy().render(
        agent_name="Bot",
        agent_description="helper",
        instructions="Focus on Python.",
    )
    assert rendered.startswith("You are Bot, helper.")
    assert "## Environment" in rendered
    assert "## Developer Instructions" in rendered
    assert "Focus on Python." in rendered


def test_prepend_puts_agent_before_base() -> None:
    rendered = InstructionPolicy().render(
        agent_name="Bot",
        agent_description="helper",
        instructions=Instruction(text="FIRST", mode=InstructionMode.PREPEND),
    )
    assert rendered.startswith("FIRST\n\nYou are Bot, helper.")


def test_include_base_false_omits_environment() -> None:
    rendered = InstructionPolicy(include_base_instructions=False).render(
        agent_name="Bot",
        agent_description="helper",
        instructions="custom",
    )
    assert rendered == "You are Bot, helper.\n\ncustom"
    assert "## Environment" not in rendered


def test_capability_sections_gated() -> None:
    features = AdapterFeatures(capabilities={Capability.MEMORY, Capability.CONTACTS})
    rendered = InstructionPolicy(features=features).render(
        agent_name="Bot",
        agent_description="helper",
    )
    assert "## Memory Tools" in rendered
    assert "## Contact Management Tools" in rendered


def test_render_system_prompt_matches_policy_append() -> None:
    """Bridge: legacy render_system_prompt delegates to InstructionPolicy."""
    via_render = render_system_prompt(
        agent_name="Bot",
        agent_description="helper",
        custom_section="Focus on Python.",
        features=AdapterFeatures(capabilities={Capability.MEMORY}),
    )
    via_policy = InstructionPolicy(
        features=AdapterFeatures(capabilities={Capability.MEMORY}),
    ).render(
        agent_name="Bot",
        agent_description="helper",
        instructions="Focus on Python.",
    )
    assert via_render == via_policy
