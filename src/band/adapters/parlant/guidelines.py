"""Guidelines declared before startup and created on the live Parlant agent."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import parlant.sdk as p


@dataclass(frozen=True)
class GuidelineSpec:
    """A guideline declared before startup, created on the live agent at start.

    ``tools=None`` means "attach the adapter's default tools" — the common
    case. An explicit sequence (including ``[]``) is passed through verbatim.
    """

    condition: str | None
    action: str | None
    tools: Sequence[Any] | None
    kwargs: dict[str, Any] = field(default_factory=dict)


class GuidelineLedger:
    """Declared guidelines plus how many already exist on the current agent.

    A restart with a borrowed (still-alive) agent must only create the specs
    declared since the last pass; a fresh agent needs all of them. The owner
    calls :meth:`forget_applied` whenever the agent they were applied to is
    gone.
    """

    def __init__(self) -> None:
        self._specs: list[GuidelineSpec] = []
        self._applied = 0

    def declare(self, spec: GuidelineSpec) -> None:
        self._specs.append(spec)

    async def apply_pending(
        self, agent: p.Agent, *, default_tools: Sequence[Any]
    ) -> None:
        """Create every not-yet-applied guideline on *agent*, in order."""
        for spec in self._specs[self._applied :]:
            await agent.create_guideline(
                condition=spec.condition,
                action=spec.action,
                tools=default_tools if spec.tools is None else spec.tools,
                **spec.kwargs,
            )
            # Each successful create is a retry checkpoint: a later failure
            # never duplicates this guideline on the same agent.
            self._applied += 1

    def forget_applied(self) -> None:
        self._applied = 0
