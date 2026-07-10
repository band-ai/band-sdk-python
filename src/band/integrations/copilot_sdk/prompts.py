"""Copilot-runtime prompt-contract sections.

Text the adapter must contribute to its ``mode="replace"`` system prompt because
the Copilot CLI runtime behaves differently from a plain chat model. Kept here
(SDK contract text) rather than in the developer-facing ``custom_section`` so it
is always present and never masquerades as developer instructions.
"""

from __future__ import annotations

# Copilot's CLI runtime is a coding-agent orchestrator: after every turn that
# ends in a tool call it injects a synthetic "continue from where you left off"
# nudge, and it only marks the session idle once the model answers with a plain
# (tool-free) assistant message. Band drives it in ``mode="replace"``, so Band
# owns the whole system prompt — including the "task completion" guidance the CLI
# normally supplies (its ``last_instructions`` section). Without it, a model told
# only "reply via band_send_message; plain text isn't delivered" answers the
# nudge with yet another band_send_message, looping until the turn times out.
# This section supplies the missing contract: end the turn in plain text once the
# work is done. It is Copilot-specific (other adapters run Band's own tool loop
# and get no such nudge), so it lives on the adapter, not in shared BASE_INSTRUCTIONS.
TURN_COMPLETION_GUIDANCE = (
    "## Turn completion\n\n"
    "This runtime may prompt you to continue after you act (for example, "
    '"continue from where you left off"). Treat that as a check, not a new '
    "request. Your turn is complete once you have taken every action the current "
    "message requires — including sending your reply with band_send_message. "
    "When nothing remains to do, end the turn with a brief plain-text response "
    '(for example, "Done."); do not call band_send_message again just to report '
    "that you are finished. Plain text is not delivered to the room — which is "
    "exactly why it cleanly ends a turn."
)
