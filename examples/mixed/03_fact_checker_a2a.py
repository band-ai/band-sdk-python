# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[a2a_gateway]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Remote A2A fact checker for the mixed example.

This agent is not connected to Band by itself. It becomes a room participant
only after the mixed bridge script forwards room messages to it.

In the developer-focused scenario, this service acts like an API contract and
integration checker.

Run with:
    uv run examples/mixed/03_fact_checker_a2a.py
"""

from __future__ import annotations

import logging

from band import configure_logging
import os

import uvicorn
from a2a.helpers import new_task_from_user_message, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import (
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
    TaskUpdater,
)
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.routes.rest_routes import create_rest_routes
from starlette.applications import Starlette
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    UnsupportedOperationError,
)
from dotenv import load_dotenv


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

        if task is None:
            if context.message is None:
                raise ValueError("A2A request is missing its message")
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work(
            new_text_message(
                "Reviewing the request for API, config, and test-surface details...",
                context_id=task.context_id,
                task_id=task.id,
            ),
        )
        await updater.add_artifact(
            [Part(text=_fact_check_response(request_text))],
            name="fact_check_report",
        )
        await updater.complete()

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        raise UnsupportedOperationError()


def main() -> None:
    """Run the fact checker A2A server."""
    configure_logging(logging.INFO, extra_loggers={"band_crewai_agent": logging.INFO})
    load_dotenv()

    host = os.getenv("MIXED_FACT_HOST", "127.0.0.1")
    port = int(os.getenv("MIXED_FACT_PORT", "10121"))
    base_url = os.getenv("MIXED_FACT_URL", f"http://{host}:{port}")

    agent_card = AgentCard(
        name="Mixed Contract Checker",
        description="Deterministic contract-checking A2A service for the mixed example",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url=base_url,
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
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
        agent_card=agent_card,
        push_config_store=InMemoryPushNotificationConfigStore(),
    )
    app = Starlette(
        routes=(
            create_agent_card_routes(agent_card)
            + create_jsonrpc_routes(
                request_handler, rpc_url="/", enable_v0_3_compat=True
            )
            + create_rest_routes(request_handler, enable_v0_3_compat=True)
        )
    )

    logger.info("Starting mixed contract checker A2A server on %s", base_url)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
