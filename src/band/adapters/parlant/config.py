"""Settings for the Parlant adapter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from pydantic import PositiveFloat

from band.core.adapterconfig import BaseAdapterConfig

if TYPE_CHECKING:
    import parlant.sdk as p

# Called in on_started with the live (server, parlant_agent) for anything the
# declarative surface doesn't cover (journeys, guideline dependencies, ...).
ConfigureCallback = Callable[["p.Server", "p.Agent"], Awaitable[None]]


class ParlantAdapterConfig(BaseAdapterConfig):
    """Settings for the Parlant adapter.

    Attributes:
        name: Parlant agent name. Defaults to the Band agent's name.
        description: Parlant agent description (its behavioral instructions).
            Defaults to the Band agent's description.
        system_prompt: Full override of the created Parlant agent's
            description. Only applies to an adapter-created agent.
        custom_section: Extra instructions appended to the created agent's
            description. Ignored when ``system_prompt`` overrides the whole
            description; only applies to an adapter-created agent.
        response_timeout: Max seconds to wait for the agent's response per
            turn. A cold start (server warmup plus the first
            guideline-matching/generation round-trips) can run long on a slow
            host.
        response_poll: Seconds per polling window within that budget; the wait
            returns as soon as the response arrives, so a warm turn is fast.
    """

    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    custom_section: str | None = None
    response_timeout: PositiveFloat = 300.0
    response_poll: PositiveFloat = 30.0
