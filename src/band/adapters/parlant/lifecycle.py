"""Who owns the Parlant server and agent, and how they start and stop.

A caller-provided (borrowed) server or agent is never torn down; an
adapter-owned server is booted through ``running_parlant_server`` and closed
on release, taking an adapter-created agent with it.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from band.integrations.parlant.server import running_parlant_server

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

    import parlant.sdk as p
    from parlant.core.application import Application

logger = logging.getLogger(__name__)

# Declares the agent and everything Parlant must process during its setup
# phase, returning the agent and the server's Application.
Prepare = Callable[["p.Server"], Awaitable[tuple["p.Agent", "Application"]]]


class ServerLifecycle:
    """The live Parlant server and agent, owned or borrowed."""

    def __init__(
        self,
        *,
        server: p.Server | None,
        agent: p.Agent | None,
        nlp_service: Any | None,
        server_options: dict[str, Any],
    ) -> None:
        if agent is not None and server is None:
            raise ValueError(
                "parlant_agent requires the server it lives on; pass server= as well"
            )
        if server is not None and (nlp_service is not None or server_options):
            raise ValueError(
                "nlp_service/server_options configure the adapter-owned server; "
                "they cannot be combined with a caller-provided server="
            )
        self.server = server
        self.agent = agent
        self._creates_agent = agent is None
        self._server_options = dict(server_options)
        if nlp_service is not None:
            self._server_options["nlp_service"] = nlp_service
        self._server_cm: AbstractAsyncContextManager[p.Server] | None = None

    async def start(self, prepare: Prepare) -> Application:
        """Run *prepare* on the borrowed server, or boot an owned one with it.

        A failure releases whatever was started before re-raising: the
        caller's own cleanup only runs for failures after startup.
        """
        try:
            if self.server is None:
                return await self._boot_owned(prepare)
            self.agent, app = await prepare(self.server)
            return app
        except BaseException:
            await self.release()
            raise

    async def _boot_owned(self, prepare: Prepare) -> Application:
        prepared: tuple[p.Agent, Application] | None = None

        async def setup(server: p.Server) -> None:
            nonlocal prepared
            prepared = await prepare(server)

        server_cm = running_parlant_server(setup=setup, **self._server_options)
        # The context manager owns cleanup when its own __aenter__ fails, so it
        # is only retained (and later exited) after a successful enter.
        server = await server_cm.__aenter__()
        self._server_cm = server_cm
        assert prepared is not None
        self.server = server
        self.agent, app = prepared
        return app

    async def release(self) -> None:
        """Close an owned server; a borrowed one is left running."""
        server_cm, self._server_cm = self._server_cm, None
        if server_cm is None:
            return
        self.server = None
        if self._creates_agent:
            self.agent = None
        try:
            await server_cm.__aexit__(None, None, None)
        except Exception:
            logger.exception("Parlant server shutdown failed")
