"""
Standalone Agentic RAG graph - completely independent of Thenvoi.

Based on LangGraph Agentic RAG tutorial pattern:
https://docs.langchain.com/oss/python/langgraph/agentic-rag

This graph implements an intelligent RAG system that:
1. Decides autonomously whether retrieval is needed
2. Retrieves relevant documents if needed
3. Grades document relevance
4. Rewrites questions for better retrieval if needed
5. Generates answers based on context

It can be imported and used as a tool in any agent, or used standalone.
"""

from typing import Literal, cast
from pydantic import BaseModel, Field

from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import InMemorySaver

from langchain_community.document_loaders import WebBaseLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage

def create_rag_graph():
    """
    Creates a compiled Agentic RAG graph.

    The graph autonomously decides when to use retrieval, grades results,
    and rewrites questions if initial retrieval is poor.

    Returns:
        Compiled LangGraph that can perform intelligent RAG
    """

    # Step 1: Load and index documents
    urls = [
        "https://lilianweng.github.io/posts/2024-11-28-reward-hacking/",
        "https://lilianweng.github.io/posts/2024-07-07-hallucination/",
        "https://lilianweng.github.io/posts/2024-04-12-diffusion-video/",
    ]

    docs = [WebBaseLoader(url).load() for url in urls]
    docs_list = [item for sublist in docs for item in sublist]

    text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        chunk_size=100, chunk_overlap=50
    )
    doc_splits = text_splitter.split_documents(docs_list)

    vectorstore = InMemoryVectorStore.from_documents(
        documents=doc_splits, embedding=OpenAIEmbeddings()
    )
    retriever = vectorstore.as_retriever()

    # Step 2: Create retriever tool
    @tool
    def retrieve_blog_posts(query: str) -> str:
        """Search and return information about Lilian Weng blog posts on AI topics like reward hacking, hallucination, and diffusion models."""
        docs = retriever.invoke(query)
        return "\n\n".join(doc.page_content for doc in docs)

    retriever_tool = retrieve_blog_posts

    # Step 3: Set up LLMs
    response_model = ChatOpenAI(model="gpt-5.4-mini", temperature=0)
    grader_model = ChatOpenAI(model="gpt-5.4-mini", temperature=0)

    # Step 4: Define graph nodes

    def generate_query_or_respond(state: MessagesState):
        """
        LLM decides whether to retrieve documents or answer directly.

        This is the key "agentic" behavior - the model autonomously decides
        if it needs more information.
        """
        system_prompt = """You are a helpful assistant answering questions about AI topics.

You have access to a retriever tool that searches blog posts about reward hacking, hallucination, and diffusion models.

**When to use the retriever:**
- User asks about specific AI topics (reward hacking, hallucination, diffusion, etc.)
- You need factual information you don't have
- Question requires technical details from blog posts

**When to answer directly:**
- General questions you can answer without retrieval
- Greetings or casual conversation
- Follow-up questions where you already have context from previous retrieval

Make intelligent decisions about when retrieval is truly needed."""

        messages = state["messages"]

        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_prompt)] + messages

        response = response_model.bind_tools([retriever_tool]).invoke(messages)

        return {"messages": [response]}

    class GradeDocuments(BaseModel):
        """Binary score for document relevance."""

        binary_score: str = Field(
            description="Relevance score: 'yes' if relevant, or 'no' if not relevant"
        )

    def grade_documents(
        state: MessagesState,
    ) -> Literal["generate_answer", "rewrite_question"]:
        """
        Grade retrieved documents for relevance.

        Routes to answer generation if relevant, otherwise rewrites question.
        """
        last_message = state["messages"][-1]
        question = state["messages"][0].content
        context = last_message.content

        grade_prompt = f"""You are a grader assessing relevance of a retrieved document to a user question.

Here is the retrieved document:

{context}

Here is the user question: {question}

If the document contains keyword(s) or semantic meaning related to the user question, grade it as relevant.
Give a binary score 'yes' or 'no' to indicate whether the document is relevant to the question."""

        response = cast(
            GradeDocuments,
            grader_model.with_structured_output(GradeDocuments).invoke(
                [{"role": "user", "content": grade_prompt}]
            ),
        )

        if response.binary_score == "yes":
            return "generate_answer"
        else:
            return "rewrite_question"

    def rewrite_question(state: MessagesState):
        """
        Rewrite the user question to improve retrieval.

        This helps when initial retrieval didn't find relevant documents.
        """
        messages = state["messages"]
        question = messages[0].content

        rewrite_prompt = f"""Look at the input and try to reason about the underlying semantic intent / meaning.

Here is the initial question:

{question}

Formulate an improved question that better captures the semantic intent:"""

        response = response_model.invoke([{"role": "user", "content": rewrite_prompt}])

        # Replace original question with rewritten version
        return {"messages": [{"role": "user", "content": response.content}]}

    def generate_answer(state: MessagesState):
        """
        Generate final answer based on retrieved context.

        Combines the original question with retrieved documents to produce
        a grounded response.
        """
        messages = state["messages"]

        # Get the retrieved context (last message should be from retriever)
        context = None
        for msg in reversed(messages):
            if hasattr(msg, "name") and msg.name == "retrieve_blog_posts":
                context = msg.content
                break

        if not context:
            # No context found, answer directly
            return {"messages": [response_model.invoke(messages)]}

        # Find original question (first user message)
        question = messages[0].content

        answer_prompt = f"""You are a helpful assistant answering questions about AI topics.

Use the following retrieved context to answer the user's question. Keep your answer concise (3 sentences max).

Context: {context}

Question: {question}

Answer:"""

        response = response_model.invoke([{"role": "user", "content": answer_prompt}])

        return {"messages": [response]}

    # Step 5: Build the graph
    workflow = StateGraph(MessagesState)

    # Add nodes (grade_documents is an edge function, not a node!)
    workflow.add_node("generate_query_or_respond", generate_query_or_respond)
    workflow.add_node("retrieve", ToolNode([retriever_tool]))
    workflow.add_node("rewrite_question", rewrite_question)
    workflow.add_node("generate_answer", generate_answer)

    # Add edges
    workflow.add_edge(START, "generate_query_or_respond")

    # If model decides to use retrieval, go to retrieve node
    # Otherwise end (model answered directly)
    workflow.add_conditional_edges(
        "generate_query_or_respond",
        tools_condition,
        {
            "tools": "retrieve",
            END: END,
        },
    )

    # After retrieval, grade documents and route accordingly
    # grade_documents is the edge function that returns routing key
    workflow.add_conditional_edges("retrieve", grade_documents)

    # After rewriting, loop back to try retrieval again
    workflow.add_edge("rewrite_question", "generate_query_or_respond")

    # After generating answer, we're done
    workflow.add_edge("generate_answer", END)

    return workflow.compile(checkpointer=InMemorySaver())
