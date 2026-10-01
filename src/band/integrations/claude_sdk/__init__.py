"""
Claude Agent SDK integration for Band SDK.

NOTE: The old BandClaudeSDKAgent has been removed.
Use the new composition-based pattern instead:

    from band import Agent
    from band.adapters import ClaudeSDKAdapter, ClaudeSDKAdapterConfig

    adapter = ClaudeSDKAdapter()  # pins the adapter's DEFAULT_MODEL
    # Or: ClaudeSDKAdapter(ClaudeSDKAdapterConfig(model="opus"))
    agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
    await agent.run()

Internal modules (session_manager, prompts) are used by the new adapter.
"""

from .prompts import generate_claude_sdk_agent_prompt
from .session_manager import ClaudeSessionManager

__all__ = [
    "ClaudeSessionManager",
    "generate_claude_sdk_agent_prompt",
]
