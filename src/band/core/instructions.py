"""Unified instruction types and system-prompt composition."""

from __future__ import annotations

from enum import StrEnum

from pydantic import ConfigDict, Field
from band.core.bases import FrozenModel

from band.core.types import AdapterFeatures, Capability


class InstructionMode(StrEnum):
    """How an instruction layer combines with layers composed so far."""

    APPEND = "append"
    PREPEND = "prepend"
    REPLACE = "replace"


class Instruction(FrozenModel):
    """Agent- or run-level instruction with an explicit composition mode.

    A bare ``str`` passed to ``instructions=`` means ``APPEND``.
    """

    text: str = Field(min_length=1)
    mode: InstructionMode = InstructionMode.APPEND


def normalize_instructions(
    value: str | Instruction | None,
) -> Instruction | None:
    """Coerce ``str | Instruction | None`` to ``Instruction | None``.

    Empty / whitespace-only strings become ``None`` (skipped layers).
    """
    if value is None:
        return None
    if isinstance(value, Instruction):
        return value if value.text.strip() else None
    text = value.strip()
    if not text:
        return None
    return Instruction(text=text, mode=InstructionMode.APPEND)


class InstructionPolicy(FrozenModel):
    """Render the SDK's base instructions plus the agent's own into one string.

    Empty instructions are skipped. ``InstructionMode.REPLACE`` drops the base
    entirely, matching legacy ``system_prompt=``.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    include_base_instructions: bool = True
    features: AdapterFeatures | None = None
    extra_sections: tuple[str, ...] = ()

    def render(
        self,
        *,
        agent_name: str,
        agent_description: str,
        instructions: str | Instruction | None = None,
    ) -> str:
        """Build the system prompt: base identity, then the agent's own text.

        ``REPLACE`` drops the base entirely (legacy ``system_prompt=``
        semantics); ``PREPEND`` puts the agent's text first.
        """
        agent = normalize_instructions(instructions)
        if agent is not None and agent.mode is InstructionMode.REPLACE:
            return agent.text.strip()

        base = self._build_base_text(agent_name, agent_description).strip()
        if agent is None:
            return base

        text = self._format_agent_text(agent).strip()
        if not text:
            return base
        if not base:
            return text
        if agent.mode is InstructionMode.PREPEND:
            return f"{text}\n\n{base}"
        return f"{base}\n\n{text}"

    def _format_agent_text(self, instruction: Instruction) -> str:
        if (
            instruction.mode == InstructionMode.APPEND
            and self.include_base_instructions
        ):
            return f"## Developer Instructions\n\n{instruction.text}"
        return instruction.text

    def _build_base_text(self, agent_name: str, agent_description: str) -> str:
        # Lazy import: prompts.render_system_prompt delegates here.
        from band.runtime.prompts import (
            BASE_INSTRUCTIONS,
            CONTACT_SECTION,
            MEMORY_SECTION,
        )

        identity = f"You are {agent_name}, {agent_description}."
        extras = tuple(s.strip() for s in self.extra_sections if s.strip())

        if not self.include_base_instructions:
            return "\n\n".join([identity, *extras])

        parts = [identity, BASE_INSTRUCTIONS.strip()]
        if self.features:
            if Capability.MEMORY in self.features.capabilities:
                parts.append(MEMORY_SECTION.strip())
            if Capability.CONTACTS in self.features.capabilities:
                parts.append(CONTACT_SECTION.strip())
        parts.extend(extras)
        return "\n\n".join(parts)
