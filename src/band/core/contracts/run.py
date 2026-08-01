"""AgentBackend run result and start context."""

from __future__ import annotations

from pydantic import ConfigDict
from band.core.bases import FrozenModel

from band.core.contracts.delivery import DeliveryReceipt
from band.core.types import TurnUsage


class RunResult(FrozenModel):
    """Outcome of ``AgentBackend.run``.

    Reply text, usage, and delivery only — turn events go through
    ``RunContext.events``. ``text`` is ``None`` when the turn produced no
    assistant text (a tool-only or narration-only turn); ``""`` is a real
    empty reply and stays distinct from it.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str | None = None
    usage: TurnUsage | None = None
    delivery: DeliveryReceipt | None = None


class BackendContext(FrozenModel):
    """Agent-level context for ``AgentBackend.start``."""

    agent_name: str = ""
    agent_description: str = ""
