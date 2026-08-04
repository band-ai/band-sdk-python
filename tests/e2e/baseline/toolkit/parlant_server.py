"""E2E wrapper around the SDK's hang-free in-process Parlant server helper.

The lifecycle mechanics (port reservation, setup-only ``__aenter__``, teardown
that cancels the serve-forever ``__aexit__`` after Parlant's own cleanup ran)
live in ``band.integrations.parlant.server.running_parlant_server`` — the same
helper the ``ParlantAdapter`` uses for its adapter-owned server. This wrapper
only adds the E2E default the SDK deliberately doesn't have: ``nlp_service``
defaults to OpenAI, because the E2E env provides ``OPENAI_API_KEY`` while
Parlant's own default (Emcie's hosted service) needs an ``EMCIE_API_KEY`` the
env doesn't set.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import parlant.sdk as p

from band.integrations.parlant.server import (
    running_parlant_server as sdk_running_parlant_server,
)


@asynccontextmanager
async def running_parlant_server(
    **server_kwargs: Any,
) -> AsyncGenerator[p.Server, None]:
    """Yield a ready in-process Parlant ``Server`` with the E2E OpenAI default."""
    server_kwargs.setdefault("nlp_service", p.NLPServices.openai)
    async with sdk_running_parlant_server(**server_kwargs) as server:
        yield server
