"""
Anthropic integration for Band SDK.

NOTE: The old BandAnthropicAgent has been removed.
Use the new composition-based pattern instead:

    from band import Agent
    from band.adapters import AnthropicAdapter

    adapter = AnthropicAdapter(model="claude-sonnet-4-5-20250929")
    agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
    await agent.run()
"""

__all__: list[str] = []
