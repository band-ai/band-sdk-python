# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[a2a_gateway]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Remote A2A risk reviewer for the mixed example.

This agent is not connected to Band by itself. It becomes a room participant
only after the mixed bridge script forwards room messages to it.

In the developer-focused scenario, this service acts like a rollout and
backward-compatibility reviewer.

Run with:
    uv run examples/mixed/04_risk_reviewer_a2a.py
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


def _risk_review_response(request_text: str) -> str:
    """Build a deterministic risk review response."""
    return "\n".join(
        [
            "Risk review notes:",
            f"- Request in scope: {request_text}",
            "- Risk 1: the change may break existing clients if request shape, auth, or defaults shifted without a compatibility note.",
            "- Risk 2: onboarding can fail if README steps or required env vars drift from the current code path.",
            "- Risk 3: runtime behavior can differ from local smoke tests if async paths, streaming paths, or bridge startup are not exercised.",
            "- Mitigation: call out backward compatibility, migration steps, and rollback expectations explicitly.",
            "- Mitigation: include observability notes so a developer knows what to watch after deploy.",
            "- Hand-off: the writer should include a risks and mitigations section in the final note.",
        ]
    )


class RiskReviewerExecutor(AgentExecutor):
    """A2A executor that returns deterministic rollout-risk guidance."""

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
                "Reviewing the request for rollout, compatibility, and rollback risks...",
                context_id=task.context_id,
                task_id=task.id,
            ),
        )
        await updater.add_artifact(
            [Part(text=_risk_review_response(request_text))],
            name="risk_review_report",
        )
        await updater.complete()

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        raise UnsupportedOperationError()


def main() -> None:
    """Run the risk reviewer A2A server."""
    configure_logging(logging.INFO, extra_loggers={"band_crewai_agent": logging.INFO})
    load_dotenv()

    host = os.getenv("MIXED_RISK_HOST", "127.0.0.1")
    port = int(os.getenv("MIXED_RISK_PORT", "10122"))
    base_url = os.getenv("MIXED_RISK_URL", f"http://{host}:{port}")

    agent_card = AgentCard(
        name="Mixed Risk Reviewer",
        description="Deterministic rollout-risk A2A service for the mixed example",
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
                id="risk-review",
                name="Risk Review",
                description="Returns compatibility, rollout, rollback, and observability risks",
                tags=["mixed-example", "risk-review"],
                examples=["Review an SDK change for rollout and compatibility risks"],
            )
        ],
    )

    request_handler = DefaultRequestHandler(
        agent_executor=RiskReviewerExecutor(),
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

    logger.info("Starting mixed risk reviewer A2A server on %s", base_url)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
