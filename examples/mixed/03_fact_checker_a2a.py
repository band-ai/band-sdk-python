# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[a2a]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
External A2A fact checker for the mixed example.

This agent is not connected to Thenvoi by itself. It becomes a room participant
only after the mixed bridge script forwards room messages to it.

In the developer-focused scenario, this service acts like an API contract and
integration checker.

Run with:
    uv run examples/mixed/03_fact_checker_a2a.py
"""

from __future__ import annotations

import logging
import os

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import (
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
    TaskUpdater,
)
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Part,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import new_agent_text_message, new_task
from a2a.utils.errors import ServerError
from dotenv import load_dotenv

from setup_logging import setup_logging

logger = logging.getLogger(__name__)


def _fact_check_response(request_text: str) -> str:
    """Build a deterministic contract-checking response."""
    return "\n".join(
        [
            "Contract check notes:",
            f"- Request in scope: {request_text}",
            "- Confirm the exact API surface that changed: method names, payload fields, headers, and expected status codes.",
            "- Confirm any new env vars, credentials, ports, or config keys required for the integration to work.",
            "- Check whether README commands, example payloads, and onboarding steps still match the running code.",
            "- Check whether tests cover the changed path and note any missing regression coverage.",
            "- Hand-off: the writer should include a short implementation-facts section in the final note.",
        ]
    )


class FactCheckerExecutor(AgentExecutor):
    """A2A executor that returns deterministic contract-checking guidance."""

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        request_text = context.get_user_input()
        task = context.current_task

        if not task:
            task = new_task(context.message)  # type: ignore[arg-type]
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.update_status(
            TaskState.working,
            new_agent_text_message(
                "Reviewing the request for API, config, and test-surface details...",
                task.context_id,
                task.id,
            ),
        )
        await updater.add_artifact(
            [Part(root=TextPart(text=_fact_check_response(request_text)))],
            name="fact_check_report",
        )
        await updater.complete()

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        raise ServerError(error=UnsupportedOperationError())


def main() -> None:
    """Run the fact checker A2A server."""
    setup_logging()
    load_dotenv()

    host = os.getenv("MIXED_FACT_HOST", "127.0.0.1")
    port = int(os.getenv("MIXED_FACT_PORT", "10121"))
    base_url = os.getenv("MIXED_FACT_URL", f"http://{host}:{port}")

    agent_card = AgentCard(
        name="Mixed Contract Checker",
        description="Deterministic contract-checking A2A service for the mixed example",
        url=f"{base_url}/",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        skills=[
            AgentSkill(
                id="fact-check",
                name="Contract Check",
                description="Returns API, config, and test-surface details for a change",
                tags=["mixed-example", "contract-check"],
                examples=[
                    "Check an SDK integration change for mismatched docs and config"
                ],
            )
        ],
    )

    request_handler = DefaultRequestHandler(
        agent_executor=FactCheckerExecutor(),
        task_store=InMemoryTaskStore(),
        push_config_store=InMemoryPushNotificationConfigStore(),
    )
    app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    ).build()

    logger.info("Starting mixed contract checker A2A server on %s", base_url)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
