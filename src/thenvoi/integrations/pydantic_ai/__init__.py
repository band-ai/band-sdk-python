"""
Pydantic AI integration for Band SDK.

NOTE: The old BandPydanticAgent has been removed.
Use the new composition-based pattern instead:

    from thenvoi import Agent
    from thenvoi.adapters import PydanticAIAdapter

    adapter = PydanticAIAdapter(model="openai:gpt-4o")
    agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
    await agent.run()
"""

__all__: list[str] = []
