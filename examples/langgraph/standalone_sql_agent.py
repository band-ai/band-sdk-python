"""
Standalone SQL Agent - A real working example that queries databases.

This agent can answer natural language questions about SQL databases by:
1. Listing available tables
2. Examining table schemas
3. Generating SQL queries
4. Validating queries
5. Executing queries and returning results

Based on the official LangGraph SQL Agent tutorial:
https://docs.langchain.com/oss/python/langgraph/sql-agent

This is a complete, functional example with:
- Real SQLite database (Chinook sample database)
- Multiple database interaction tools
- ReAct-style agent that reasons about database structure
- Query validation before execution
"""

import os
from typing import Annotated, Literal
from typing_extensions import TypedDict
from langchain_core.messages import HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import ToolNode


class MessagesState(TypedDict):
    """State for the SQL agent."""

    messages: Annotated[list, add_messages]


def create_sql_agent(db_path: str = "Chinook.db"):
    """
    Creates a SQL agent that can query databases using natural language.

    Args:
        db_path: Path to SQLite database file. Defaults to Chinook sample database.

    Returns:
        Compiled LangGraph that can answer questions about the database

    Example questions:
        - "Which genre has the longest tracks?"
        - "What are the top 5 albums by number of tracks?"
        - "List all employees and their titles"
    """
    # Initialize database connection
    db = SQLDatabase.from_uri(f"sqlite:///{db_path}")

    # Create LLM for the agent
    llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini"), temperature=0)

    # Create SQL toolkit with database interaction tools
    # This provides: sql_db_list_tables, sql_db_schema, sql_db_query, sql_db_query_checker
    toolkit = SQLDatabaseToolkit(db=db, llm=llm)
    tools = toolkit.get_tools()

    # Bind tools to LLM
    llm_with_tools = llm.bind_tools(tools)

    # Define agent node
    def agent_node(state: MessagesState):
        """Agent decides what to do based on conversation history."""
        return {"messages": [llm_with_tools.invoke(state["messages"])]}

    # Create tool node for executing tools
    tool_node = ToolNode(tools)

    # Routing function
    def should_continue(state: MessagesState) -> Literal["tools", END]:
        """Determine if we should continue or end."""
        last_message = state["messages"][-1]
        # If there are no tool calls, we finish
        if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
            return END
        return "tools"

    # Build the graph
    workflow = StateGraph(MessagesState)

    # Add nodes
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)

    # Add edges
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", should_continue, ["tools", END])
    workflow.add_edge("tools", "agent")

    # Compile with checkpointer for conversation memory
    return workflow.compile(checkpointer=InMemorySaver())


def download_chinook_db():
    """Download the Chinook sample database if not present."""
    import logging
    import os
    import urllib.request

    logger = logging.getLogger(__name__)

    db_path = "Chinook.db"
    if os.path.exists(db_path):
        logger.info("Database already exists at %s", db_path)
        return db_path

    logger.info("Downloading Chinook sample database...")
    url = "https://github.com/lerocha/chinook-database/raw/master/ChinookDatabase/DataSources/Chinook_Sqlite.sqlite"

    try:
        urllib.request.urlretrieve(url, db_path)
        logger.info("Downloaded database to %s", db_path)
        return db_path
    except Exception as e:
        logger.error("Error downloading database: %s", e)
        logger.info("Creating minimal test database instead...")

        # Create minimal test database if download fails
        import sqlite3

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE employees (
                id INTEGER PRIMARY KEY,
                name TEXT,
                title TEXT,
                salary INTEGER
            )
        """)

        cursor.executemany(
            "INSERT INTO employees (name, title, salary) VALUES (?, ?, ?)",
            [
                ("Alice Johnson", "Software Engineer", 95000),
                ("Bob Smith", "Product Manager", 110000),
                ("Carol Davis", "Data Scientist", 105000),
                ("David Brown", "Designer", 85000),
            ],
        )

        conn.commit()
        conn.close()
        logger.info("Created minimal test database at %s", db_path)
        return db_path


# Export for easy import
if __name__ == "__main__":
    import asyncio
    import logging

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    async def test():
        """Test the SQL agent standalone."""
        # Download database if needed
        db_path = download_chinook_db()

        # Create SQL agent
        sql_agent = create_sql_agent(db_path)

        # Test queries
        questions = [
            "List all the tables in the database",
            "What are the column names in the Employee table?",
            "How many employees are there?",
        ]

        logger.info("=" * 60)
        logger.info("Testing SQL Agent")
        logger.info("=" * 60)

        for question in questions:
            logger.info("Question: %s", question)
            logger.info("-" * 60)

            result = await sql_agent.ainvoke(
                {"messages": [HumanMessage(content=question)]},
                {"configurable": {"thread_id": "test-session"}},
            )

            # Get the final AI response
            final_message = result["messages"][-1]
            if isinstance(final_message, AIMessage):
                logger.info("Answer: %s", final_message.content)
            else:
                logger.info("Response: %s", final_message)

    asyncio.run(test())
