"""
Claude Agent SDK integration for Band SDK.

NOTE: The old BandClaudeSDKAgent has been removed.
Use the new composition-based pattern instead:

    from band import Agent
    from band.adapters import ClaudeSDKAdapter

    adapter = ClaudeSDKAdapter()  # uses npm `claude` binary's default model
    # Or: ClaudeSDKAdapter(model="opus", fallback_model="sonnet")
    agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
    await agent.run()

Internal modules (session_manager, prompts) are used by the new adapter.
"""

from .session_manager import ClaudeSessionManager
from .prompts import generate_claude_sdk_agent_prompt

__all__ = [
    "ClaudeSessionManager",
    "generate_claude_sdk_agent_prompt",
]
