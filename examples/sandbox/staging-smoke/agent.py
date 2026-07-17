# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[langgraph]"]
#
# [tool.uv]
# override-dependencies = ["websockets>=16"]
# ///
# The override matters behind a proxy (Docker Sandbox included): the proxy
# answers CONNECT with `HTTP/1.0 200`, which websockets <= 15.0.1 rejects
# ("did not receive a valid HTTP response from proxy" — fixed in 16.0), so
# the SDK's WebSocket can never connect while REST keeps working. langgraph-sdk
# pins websockets<16 (no fixed 15.x exists), forcing the broken version unless
# overridden. Safe here: this graph runs locally and never uses langgraph-sdk's
# own WebSocket client, which is the only thing that pin protects.
"""
Deterministic sandboxed agent for the Docker Sandbox staging smoke.

Installs `band-sdk[langgraph]` from PyPI, not this repo's own git source
(unlike most examples elsewhere in this repo): a `uv`/`pip` git install does a
full clone, and this repo carries a private, SSH-only `.claude` submodule
(`.gitmodules`) that a sandboxed environment has no credentials for — its
submodule init fails there even though it has nothing to do with the `band`
package itself. PyPI's `band-sdk` is a plain sdist/wheel with no such step,
and (checked when this was written) it isn't behind git HEAD.

Runs headlessly inside a real Docker Sandbox (`sbx exec`) against staging. It
calls no model: on a mentioned message it extracts a `marker:<token>` from the
content, resolves the sending user's mention handle via `band_get_participants`,
and replies with `sandbox-ack:<token>` via `band_send_message` — proving the
SDK's normal WebSocket-receive + REST-reply round trip from inside the sandbox.

`LangGraphAdapter` never relays a graph's plain text back to the room
automatically, so the reply must go through `band_send_message` explicitly;
calling it also satisfies the platform's mention requirement.

Unlike other examples, this one's own Band identity (`BAND_AGENT_ID` /
`BAND_API_KEY`) is not read from a checked-in `agent_config.yaml` via
`load_agent_config`. `run.sh` provisions the agent fresh for each run (see
`probe.py --label provision`, which reuses the SDK's own E2E baseline
toolkit) and injects the newly-minted credentials as environment variables;
there is no static credential to source here.

Run with (invoked by run.sh inside the sandbox):
    uv run agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from langgraph.graph import END, MessagesState, StateGraph

from band import Agent
from band.adapters import LangGraphAdapter
from band.runtime.types import normalize_handle

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

_MARKER_PATTERN = re.compile(r"marker:(\S+)")


def extract_marker(content: str) -> str:
    match = _MARKER_PATTERN.search(content)
    if not match:
        raise ValueError(f"no marker found in message content: {content!r}")
    return match.group(1)


def ensure_not_error(result: Any, *, tool_name: str) -> Any:
    """Band platform tools return an *error string* instead of raising on an
    unexpected failure (see `AgentTools.execute_tool_call`'s docstring) — that
    convention exists for LLM callers, which read the string and react. This
    graph has no LLM to notice it, so check explicitly and raise a diagnosable
    error instead of, say, iterating an error string as if it were the
    expected list of participants.
    """
    if isinstance(result, str):
        raise RuntimeError(f"{tool_name} failed: {result}")
    return result


def deterministic_graph_factory(band_tools: list[Any]) -> Any:
    """Build a no-LLM graph: read the marker, mention the sender, ack it."""
    tools_by_name = {tool.name: tool for tool in band_tools}
    get_participants = tools_by_name["band_get_participants"]
    send_message = tools_by_name["band_send_message"]

    async def respond(state: MessagesState) -> dict[str, list[Any]]:
        marker = extract_marker(str(state["messages"][-1].content))

        participants = ensure_not_error(
            await get_participants.ainvoke({}), tool_name="band_get_participants"
        )
        sender = next(
            (p for p in participants if p.get("type") != "Agent" and p.get("handle")),
            None,
        )
        if sender is None:
            raise RuntimeError("no mentionable (non-agent) participant in room")
        handle = normalize_handle(sender["handle"])
        if handle is None:
            raise RuntimeError(f"participant has no handle to mention: {sender!r}")

        ensure_not_error(
            await send_message.ainvoke(
                {"content": f"sandbox-ack:{marker}", "mentions": [handle]}
            ),
            tool_name="band_send_message",
        )
        return {"messages": []}

    graph = StateGraph(MessagesState)
    graph.add_node("respond", respond)
    graph.set_entry_point("respond")
    graph.add_edge("respond", END)
    return graph.compile()


async def main() -> None:
    ws_url = os.getenv("BAND_WS_URL")
    rest_url = os.getenv("BAND_REST_URL")
    agent_id = os.getenv("BAND_AGENT_ID")
    api_key = os.getenv("BAND_API_KEY")

    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")
    if not agent_id:
        raise ValueError("BAND_AGENT_ID environment variable is required")
    if not api_key:
        raise ValueError("BAND_API_KEY environment variable is required")

    adapter = LangGraphAdapter(graph_factory=deterministic_graph_factory)
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Sandbox smoke agent starting (agent_id=%s)", agent_id)
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
