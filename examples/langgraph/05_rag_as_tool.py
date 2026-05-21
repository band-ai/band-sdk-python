# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[langgraph]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Example: Using the standalone Agentic RAG graph with Band platform.

This example demonstrates:
1. Importing a standalone Agentic RAG graph (following LangGraph tutorial pattern)
2. Wrapping it as a tool using graph_as_tool
3. Adding it to a Band agent alongside platform tools
4. The agent can delegate research questions to the RAG system

The RAG graph:
- Autonomously decides when retrieval is needed
- Grades retrieved documents for relevance
- Rewrites questions for better retrieval if needed
- Generates grounded answers based on retrieved context

Pattern:
- Main agent handles chat interactions (thenvoi_send_message, thenvoi_add_participant, etc.)
- RAG subgraph handles intelligent document retrieval and question answering
- User asks questions → Agent delegates to RAG → Agent sends response

Run with (from repo root):
    uv run examples/langgraph/05_rag_as_tool.py

Note: Must be run from repo as it imports standalone_rag.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from standalone_rag import create_rag_graph

from setup_logging import setup_logging
from band import Agent
from band.adapters import LangGraphAdapter
from band.config import load_agent_config
from band.integrations.langgraph import graph_as_tool

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()
    ws_url = os.getenv("THENVOI_WS_URL")
    rest_url = os.getenv("THENVOI_REST_URL")

    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is required")

    # Load agent configuration from agent_config.yaml
    agent_id, api_key = load_agent_config("rag_agent")

    if agent_id:
        logger.info("Using existing agent ID from config: %s", agent_id)

    logger.info("Step 1: Creating standalone Agentic RAG graph...")
    logger.info("(This may take a moment to load and index blog posts)")
    rag_graph = create_rag_graph()
    logger.info(
        "RAG graph created - it has autonomous retrieval, grading, and rewriting capabilities"
    )

    logger.info("\nStep 2: Wrapping RAG graph as a tool...")
    rag_tool = graph_as_tool(
        graph=rag_graph,
        name="research_ai_topics",
        description="Use this tool to research AI topics like reward hacking, hallucination, and diffusion models. The tool intelligently decides when to retrieve documents and can rewrite questions for better results.",
        input_schema={
            "messages": (
                "A list of message objects for the research question. "
                "Each message should have 'role' and 'content' keys. "
                "Example: [{'role': 'user', 'content': 'What is reward hacking?'}]"
            )
        },
        # Extract the final answer from the RAG agent's messages
        result_formatter=lambda state: (
            state["messages"][-1].content if state.get("messages") else "No result"
        ),
        # Enable memory: RAG graph will remember conversation context within the same room
        isolate_thread=False,
    )
    logger.info("RAG graph wrapped as a tool with memory enabled")

    logger.info("\nStep 3: Creating main Band agent with RAG tool...")

    # Custom instructions for using the RAG tool
    rag_instructions = """

## RAG Research Tool

You have access to `research_ai_topics` tool that can answer questions about AI topics
by retrieving information from Lilian Weng's blog posts.

### When to Use RAG Tool:
- Questions about: reward hacking, hallucination, diffusion models, video generation
- Technical AI questions that need factual information
- When user explicitly asks to research or look up something

### How to Use It:
When someone asks a question about AI topics:
1. Use `research_ai_topics` with the question
2. Get the researched answer from the tool
3. Use `thenvoi_send_message` to send the answer back to the chat

### "Tell X about Y" Pattern:
When a user says "tell [Person/Agent] about [Topic]":
1. Get their info: `thenvoi_get_participants()` to find their ID and username
2. Research topic: `research_ai_topics` to get information about the topic
3. Send with mention: `thenvoi_send_message` with "@Username, [information]" and mentions parameter

**Example:**
User: "tell nvidia about reward hacking"
1. thenvoi_get_participants() → find Nvidia_Agent
2. research_ai_topics(messages=[{'role': 'user', 'content': 'What is reward hacking?'}]) → get answer
3. thenvoi_send_message(content="@Nvidia_Agent, [answer from research]", mentions='[{"id":"xxx","username":"Nvidia_Agent"}]')
"""

    # Create adapter with RAG tool
    adapter = LangGraphAdapter(
        llm=ChatOpenAI(model="gpt-4o"),
        checkpointer=InMemorySaver(),
        additional_tools=[rag_tool],
        custom_section=rag_instructions,
    )

    # Create and start agent
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting agent with RAG tool...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
