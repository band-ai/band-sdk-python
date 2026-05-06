"""Public protocol-level constants for the LangGraph integration.

These keys form the contract between :class:`LangGraphAdapter` and any
user-built graph (factory or static) that wants to read adapter-owned state
out of the ``RunnableConfig``. They live in the integrations layer so that
custom graphs can import them without depending on ``thenvoi.adapters``.
"""

from __future__ import annotations

THENVOI_SYSTEM_PROMPT_CONFIG_KEY = "thenvoi_system_prompt"
"""Key under ``config["configurable"]`` that carries the adapter-rendered
system prompt for the current turn. Custom graphs that own their own prompt
node should read this and inject it themselves rather than relying on the
adapter's bootstrap-time system message."""
