"""Standalone A2A counterparty server for baseline E2E smokes.

Built directly on a2a-sdk's own server primitives (not Band's gateway), so
``test_a2a.py`` can point a live ``A2AAdapter`` at a real, independent A2A
implementation -- proving the SDK's outbound client against something other
than our own gateway. Scripted, not LLM-backed, so it needs no LLM key: a
request whose text carries ``ERROR_MARKER`` fails the task; every other
request completes with ``CANNED_REPLY``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import uvicorn
from a2a.helpers import get_message_text, new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    UnsupportedOperationError,
)
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT
from starlette.applications import Starlette

from band.integrations.uvicorn_server import wait_until_started

from tests.ports import reserve_port

CANNED_REPLY = "a2a-fixture-canned-reply"
ERROR_MARKER = "a2a-fixture-trigger-error"

# Mirrors band.integrations.a2a.gateway.server.SERVER_STOP_TIMEOUT_S: uvicorn's
# own default (None) waits forever for an open connection to close on stop(),
# and a live message:stream response has no other way to end on its own.
STOP_TIMEOUT_S = 5

# How long start() waits for uvicorn to report ready before giving up.
START_TIMEOUT_S = 5


class ScriptedExecutor(AgentExecutor):
    """A deterministic counterparty: a canned reply, or a scripted failure.

    No LLM involved -- ``ERROR_MARKER`` in the request text is the only
    branch, so a smoke can trigger either path deterministically.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.message is None:
            raise ValueError("A2A request is missing its message")
        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        if ERROR_MARKER in get_message_text(context.message):
            await updater.failed(
                updater.new_agent_message([Part(text="fixture: scripted failure")])
            )
            return
        await updater.complete(updater.new_agent_message([Part(text=CANNED_REPLY)]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise UnsupportedOperationError()


class A2ACounterparty:
    """A live, standalone A2A JSON-RPC server for a smoke to point an
    ``A2AAdapter`` at.

    Binds an OS-assigned free port (via ``reserve_port``) so parallel runs
    never collide. The port must be known before the agent card is built (the
    card advertises it), so it is reserved up front in ``__init__`` rather
    than left to uvicorn to pick at ``start()`` time.
    """

    def __init__(self) -> None:
        self.port = reserve_port()
        self._uvicorn: uvicorn.Server | None = None
        self._server_task: asyncio.Task[Any] | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _agent_card(self) -> AgentCard:
        return AgentCard(
            name="A2A Smoke Fixture",
            description="Deterministic A2A counterparty for baseline E2E smokes.",
            supported_interfaces=[
                AgentInterface(
                    protocol_binding="JSONRPC",
                    protocol_version=PROTOCOL_VERSION_CURRENT,
                    url=self.url,
                ),
            ],
            version="1.0.0",
            capabilities=AgentCapabilities(streaming=True),
            skills=[
                AgentSkill(
                    id="default",
                    name="Scripted reply",
                    description="Replies with a deterministic canned marker.",
                    tags=["smoke"],
                )
            ],
            default_input_modes=["text/plain"],
            default_output_modes=["text/plain"],
        )

    def _build_app(self) -> Starlette:
        card = self._agent_card()
        handler = DefaultRequestHandler(
            agent_executor=ScriptedExecutor(),
            task_store=InMemoryTaskStore(),
            agent_card=card,
        )
        routes = create_agent_card_routes(card) + create_jsonrpc_routes(
            handler, rpc_url="/", enable_v0_3_compat=True
        )
        return Starlette(routes=routes)

    async def start(self) -> None:
        server = uvicorn.Server(
            uvicorn.Config(
                self._build_app(),
                host="127.0.0.1",
                port=self.port,
                log_level="warning",
                timeout_graceful_shutdown=STOP_TIMEOUT_S,
            )
        )
        server_task = asyncio.create_task(server.serve())
        self._uvicorn = server
        self._server_task = server_task
        await wait_until_started(server, server_task, timeout_s=START_TIMEOUT_S)

    async def stop(self) -> None:
        if self._uvicorn is None or self._server_task is None:
            return
        # Ask uvicorn to exit rather than cancelling serve(): cancellation
        # skips its shutdown phase and leaks the listening socket.
        self._uvicorn.should_exit = True
        try:
            await self._server_task
        except asyncio.CancelledError:
            raise
        except BaseException:  # uvicorn raises SystemExit on startup failure
            pass
        self._uvicorn = None
        self._server_task = None
