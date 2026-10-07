"""
Pydantic AI integration for Band SDK.

NOTE: The old BandPydanticAgent has been removed.
Use the new composition-based pattern instead:

    from band import Agent
    from band.adapters import PydanticAIAdapter, PydanticAIAdapterConfig

    adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="openai:gpt-6-luna"))
    agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
    await agent.run()
"""

__all__: list[str] = []
