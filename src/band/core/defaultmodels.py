"""Default LLM model ids for every adapter, test, and CI lane.

The one place to update when a provider ships a newer cheap model or retires
one. Frameworks that qualify ids by provider (``openai:``, ``openai/``) prefix
these at the use site.
"""

from __future__ import annotations

# Also the Codex default: it is in Codex's own model catalogue.
OPENAI_MODEL = "gpt-6-luna"
ANTHROPIC_MODEL = "claude-sonnet-5-5"
GEMINI_MODEL = "gemini-3.8-flash"
# Cheap tier for the E2E agents and judge; the judge needs structured outputs.
ANTHROPIC_SMALL_MODEL = "claude-haiku-4-5"
