"""
Anthropic integration for Band SDK.

NOTE: The old BandAnthropicAgent has been removed.
Use the new composition-based pattern instead:

    from band import Agent
    from band.adapters import AnthropicAdapter, AnthropicAdapterConfig

    adapter = AnthropicAdapter(
        AnthropicAdapterConfig(model="claude-sonnet-5-5")
    )
    agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
    await agent.run()
"""

__all__: list[str] = []
