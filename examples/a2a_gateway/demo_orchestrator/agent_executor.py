"""A2A AgentExecutor for the Orchestrator agent.

This module provides the A2A server-side executor that bridges the
OrchestratorAgent with the A2A protocol.
"""

from __future__ import annotations

import logging

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    InternalError,
    Part,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import new_agent_text_message, new_task
from a2a.utils.errors import ServerError

try:
    from .agent import OrchestratorAgent
except ImportError:
    from agent import OrchestratorAgent

logger = logging.getLogger(__name__)


class OrchestratorAgentExecutor(AgentExecutor):
    """A2A AgentExecutor for the Orchestrator agent.

    This executor implements the A2A protocol's AgentExecutor interface,
    bridging incoming A2A requests to the OrchestratorAgent.
    """

    def __init__(self, agent: OrchestratorAgent):
        """Initialize the executor.

        Args:
            agent: The OrchestratorAgent instance to execute
        """
        self.agent = agent

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Execute a request from an A2A client.

        Args:
            context: Request context with message and task info
            event_queue: Queue for sending events back to client
        """
        query = context.get_user_input()
        task = context.current_task

        if not task:
            task = new_task(context.message)  # type: ignore
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)

        try:
            async for item in self.agent.stream(query, task.context_id):
                is_task_complete = item["is_task_complete"]
                require_user_input = item["require_user_input"]
                content = item["content"]

                if not is_task_complete and not require_user_input:
                    # Working status update
                    await updater.update_status(
                        TaskState.working,
                        new_agent_text_message(
                            content,
                            task.context_id,
                            task.id,
                        ),
                    )
                elif require_user_input:
                    # Need more input from user
                    await updater.update_status(
                        TaskState.input_required,
                        new_agent_text_message(
                            content,
                            task.context_id,
                            task.id,
                        ),
                        final=True,
                    )
                    break
                else:
                    # Task complete - add artifact and finish
                    await updater.add_artifact(
                        [Part(root=TextPart(text=content))],
                        name="orchestrator_result",
                    )
                    await updater.complete()
                    break

        except Exception as e:
            logger.error("Error executing orchestrator agent: %s", e)
            raise ServerError(error=InternalError()) from e

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Cancel a running task.

        Args:
            context: Request context
            event_queue: Event queue

        Raises:
            ServerError: Cancellation not supported
        """
        raise ServerError(error=UnsupportedOperationError())
