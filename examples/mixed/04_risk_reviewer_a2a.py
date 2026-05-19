# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[a2a]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
External A2A risk reviewer for the mixed example.

This agent is not connected to Thenvoi by itself. It becomes a room participant
only after the mixed bridge script forwards room messages to it.

In the developer-focused scenario, this service acts like a rollout and
backward-compatibility reviewer.

Run with:
    uv run examples/mixed/04_risk_reviewer_a2a.py
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

        if not task:
            task = new_task(context.message)  # type: ignore[arg-type]
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.update_status(
            TaskState.working,
            new_agent_text_message(
                "Reviewing the request for rollout, compatibility, and rollback risks...",
                task.context_id,
                task.id,
            ),
        )
        await updater.add_artifact(
            [Part(root=TextPart(text=_risk_review_response(request_text)))],
            name="risk_review_report",
        )
        await updater.complete()

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        raise ServerError(error=UnsupportedOperationError())


def main() -> None:
    """Run the risk reviewer A2A server."""
    setup_logging()
    load_dotenv()

    host = os.getenv("MIXED_RISK_HOST", "127.0.0.1")
    port = int(os.getenv("MIXED_RISK_PORT", "10122"))
    base_url = os.getenv("MIXED_RISK_URL", f"http://{host}:{port}")

    agent_card = AgentCard(
        name="Mixed Risk Reviewer",
        description="Deterministic rollout-risk A2A service for the mixed example",
        url=f"{base_url}/",
        version="1.0.0",
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
        push_config_store=InMemoryPushNotificationConfigStore(),
    )
    app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    ).build()

    logger.info("Starting mixed risk reviewer A2A server on %s", base_url)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
