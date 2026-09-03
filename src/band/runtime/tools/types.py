"""Registration metadata for a built-in Band tool: which surface it's on,
its input model, and the ``AgentTools``/``HumanTools`` method it dispatches to.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel


class Surface(StrEnum):
    """The two surfaces a built-in Band tool can be registered on."""

    AGENT = "agent"
    HUMAN = "human"


class ToolCategory(StrEnum):
    """The ``AdapterFeatures.include_categories``/``exclude_categories``
    buckets a built-in Band tool can fall into.

    Single source of truth for these five values -- every per-adapter
    category mapping (e.g. ``_TOOL_CATEGORIES``, CrewAI's
    ``_CREWAI_TOOL_CATEGORIES``) builds its dict from this enum instead of
    retyping the strings.
    """

    CHAT = "chat"
    CONTACTS = "contacts"
    MEMORY = "memory"
    FILES = "files"
    TASKS = "tasks"


@dataclass(frozen=True)
class ToolDefinition:
    """Metadata for a built-in Band tool."""

    name: str
    input_model: type[BaseModel]
    method_name: str
    surface: Surface = Surface.AGENT
