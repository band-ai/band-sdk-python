"""
LangGraph integration for Band SDK.

NOTE: The old BandLangGraphAgent has been removed.
Use the new composition-based pattern instead:

    from band import Agent
    from band.adapters import LangGraphAdapter
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import MemorySaver

    adapter = LangGraphAdapter(
        llm=ChatOpenAI(model="gpt-5.4"),
        checkpointer=MemorySaver(),
    )
    agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
    await agent.run()

Utility functions are still available:
- agent_tools_to_langchain: Convert AgentTools to LangChain tool format
- graph_as_tool: Wrap a LangGraph as a callable tool
- MessageFormatter: Protocol for message formatting
"""

from .langchain_tools import agent_tools_to_langchain
from .graph_tools import graph_as_tool
from .message_formatters import MessageFormatter, default_messages_state_formatter

__all__ = [
    # Utilities (still available)
    "agent_tools_to_langchain",
    "graph_as_tool",
    "MessageFormatter",
    "default_messages_state_formatter",
]
