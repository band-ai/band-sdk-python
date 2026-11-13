"""Default LLM model ids, and settings a pinned model needs, for every adapter,
test, and CI lane.

The one place to update when a provider ships a newer cheap model or retires
one. Frameworks that qualify ids by provider (``openai:``, ``openai/``) prefix
these at the use site.
"""

from __future__ import annotations

# Also the Codex default; Codex CLI bundles it from 0.156.1.
OPENAI_MODEL = "gpt-6-luna"
ANTHROPIC_MODEL = "claude-sonnet-5-5"
GEMINI_MODEL = "gemini-3.8-flash"
# Cheapest current Claude.
ANTHROPIC_SMALL_MODEL = "claude-haiku-4-5"
# Letta 0.16.8, the last self-hosted Letta server, never registers gpt-6-*
# handles and sends reasoning effort "minimal", which gpt-5.6-* rejects.
LETTA_SELF_HOSTED_MODEL = "openai/gpt-5.4-mini"
# Letta runs that model at reasoning effort "none", where it follows multi-part
# instructions poorly. Letta fills omitted fields with its settings defaults,
# not the model's, so the model's own values are restated to change only the
# effort.
LETTA_SELF_HOSTED_MODEL_SETTINGS = {
    "provider_type": "openai",
    "reasoning": {"reasoning_effort": "low"},
    "temperature": 1.0,
    "parallel_tool_calls": False,
    "strict": False,
}
