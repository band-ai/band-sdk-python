"""
Agno adapter using the SimpleAdapter pattern.

Agno is model-agnostic: the developer builds and configures their own Agno
``Agent`` (model, instructions, tools, reasoning, ...) and hands it to this
adapter. The adapter simply bridges it to Band — it converts Band history to
Agno messages, runs the developer's agent, and sends the text reply back.

Unlike adapters that run an explicit tool-calling loop, Agno owns its own agent
loop internally: ``Agent.arun(input=...)`` accepts a list of Agno messages and
returns a run output whose ``.content`` is the final text. This adapter is a
text-only skeleton — Band platform tools are not wired into the Agno agent yet.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import (
    AdapterFeatures,
    Capability,
    Emit,
    PlatformMessage,
)
from band.converters.agno import AgnoHistoryConverter, AgnoMessages

if TYPE_CHECKING:
    from agno.agent import Agent as AgnoAgent

logger = logging.getLogger(__name__)


class AgnoAdapter(SimpleAdapter[AgnoMessages]):
    """
    Agno framework adapter (text-only skeleton).

    Takes a developer-built Agno ``Agent`` and bridges it to Band. Stateless per
    room: Band history is the source of truth and is passed as input on every
    message. No Band platform tools are wired into the Agno agent yet.

    Example:
        from agno.agent import Agent as AgnoAgent
        from agno.models.anthropic import Claude

        agno_agent = AgnoAgent(
            model=Claude(id="claude-sonnet-4-6"),
            instructions="You are a helpful assistant.",
        )
        adapter = AgnoAdapter(agno_agent)
        agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
        await agent.run()
    """

    # Skeleton: no execution events emitted, no tool capabilities yet.
    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset()
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset()

    def __init__(
        self,
        agent: AgnoAgent,
        *,
        history_converter: AgnoHistoryConverter | None = None,
        features: AdapterFeatures | None = None,
    ) -> None:
        super().__init__(
            history_converter=history_converter or AgnoHistoryConverter(),
            features=features,
        )

        # The developer's Agno agent; reused across rooms/messages. Agno keeps
        # per-run state in its run context, so a single instance is safe to
        # reuse (Band history is passed as input on every call).
        self.agent = agent

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Sync the converter's identity with the Band agent name."""
        await super().on_started(agent_name, agent_description)

        # Keep the converter's own-agent filtering in sync with our identity.
        if isinstance(self.history_converter, AgnoHistoryConverter):
            self.history_converter.set_agent_name(agent_name)

        logger.info("Agno adapter started for agent: %s", agent_name)

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: AgnoMessages,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Run the developer's Agno agent on the history and reply with text."""
        from agno.models.message import Message

        # Band history is the source of truth; build the input fresh each call.
        messages: list[Message] = list(history)
        if participants_msg:
            messages.append(
                Message(role="user", content=f"[System]: {participants_msg}")
            )
        if contacts_msg:
            messages.append(Message(role="user", content=f"[System]: {contacts_msg}"))
        messages.append(Message(role="user", content=msg.format_for_llm()))

        try:
            response = await self.agent.arun(input=messages)
        except Exception as e:
            logger.exception("Error running Agno agent in room %s: %s", room_id, e)
            raise

        if response is None:
            return

        # get_content_as_string() handles str, structured (BaseModel -> JSON),
        # and dict/list output uniformly.
        text = response.get_content_as_string().strip()
        if not text:
            logger.debug("Room %s: Agno agent returned empty content", room_id)
            return

        if response.content_type not in ("str", ""):
            logger.debug(
                "Room %s: Agno returned %s output; sending JSON-serialized form",
                room_id,
                response.content_type,
            )

        mention = [{"id": msg.sender_id, "name": msg.sender_name or msg.sender_type}]
        await tools.send_message(text, mentions=mention)
