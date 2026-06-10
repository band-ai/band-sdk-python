"""
Pydantic AI integration for Band SDK.

NOTE: The old BandPydanticAgent has been removed.
Use the new composition-based pattern instead:

    from band import Agent
    from band.adapters import PydanticAIAdapter

    adapter = PydanticAIAdapter(model="openai:gpt-5.4")
    agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
    await agent.run()
"""

__all__: list[str] = []
