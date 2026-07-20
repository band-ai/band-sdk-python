"""Prompt construction for the Letta adapter."""

from __future__ import annotations

# Known names of the message/event send tools across the Band MCP surfaces the
# adapter can be pointed at: the SDK's self-hosted LocalMCPServer exposes the
# band_* names, the external band-mcp exposes create_agent_chat_*. The adapter
# resolves the actual names from the tools the registered server reports, so
# the enforcement prompt, silent-reporting set, and auto-relay detection all
# follow whichever server is wired — first entry doubles as the fallback.
SEND_MESSAGE_TOOL_NAMES: tuple[str, ...] = (
    "band_send_message",
    "create_agent_chat_message",
)
SEND_EVENT_TOOL_NAMES: tuple[str, ...] = (
    "band_send_event",
    "create_agent_chat_event",
)


def render_tool_enforcement(
    send_tool: str,
    event_tool: str,
    *,
    room_id: str | None = None,
) -> str:
    """Letta-specific preamble prepended to the agent's instruction block.

    In practice, models routed through Letta consistently skip tool calls
    entirely — likely because Letta injects its own system prompt that
    conflicts with ours.  This aggressive enforcement partially mitigates the
    issue but does not fully resolve it.

    ``room_id`` is included when the tool schemas carry a required ``room_id``
    argument (the self-hosted MCP server resolves tools per room at call time).
    """
    room_section = (
        (
            "## Tool arguments\n\n"
            f"Every tool call REQUIRES a `room_id` argument. Your room_id is:\n"
            f"{room_id}\n\n"
        )
        if room_id
        else ""
    )
    return f"""\
## MANDATORY: You MUST use tools to communicate

You are connected to a multi-agent chat platform via MCP tools.
Your plain text responses (assistant_message) are NOT delivered to anyone.
The ONLY way to communicate is by calling the provided tools.

EVERY response MUST include at least one tool call. Specifically:
- To send a message: call `{send_tool}` — this is REQUIRED
- To share your thinking: call `{event_tool}` with message_type="thought"
- NEVER respond with just plain text — it will be silently discarded

{room_section}## WRONG (message is lost):
Just responding with plain text like this.

## CORRECT:
Call `{send_tool}` with your reply content and the mentions.

If you respond without calling `{send_tool}`, the user sees NOTHING.

"""
