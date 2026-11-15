"""Tools that let Codex change its own model and reasoning."""

from __future__ import annotations

from pydantic import BaseModel, Field

REASONING_SUMMARIES = {"auto", "concise", "detailed", "none"}


# Codex-local slash commands, which surface their outcome in the room themselves.
SILENT_REPORTING_TOOLS: frozenset[str] = frozenset({"setmodel", "setreasoning"})


class SetModelInput(BaseModel):
    """Switch the model used for subsequent turns. Call this when a different model would be more appropriate for the task (e.g. a faster model for simple queries, a stronger model for complex reasoning)."""

    model: str = Field(description="Model ID to use (e.g. 'gpt-5.5').")


class SetReasoningInput(BaseModel):
    """Adjust reasoning effort and summary detail for subsequent turns. Use higher effort for complex problems and lower effort for straightforward tasks."""

    effort: str | None = Field(
        default=None,
        description="Reasoning effort level supported by the current model (for example low, medium, or high). Omit to keep current.",
    )
    summary: str | None = Field(
        default=None,
        description="Reasoning summary detail: auto, concise, detailed, or none. Omit to keep current.",
    )
