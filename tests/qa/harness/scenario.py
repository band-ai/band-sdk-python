from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_runner import AgentRunner
    from .api_client import AgentInfo, PlatformClient

logger = logging.getLogger(__name__)


class Status(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"
    SKIP = "SKIP"


@dataclass
class StepResult:
    action: str
    expected: str
    actual: str
    status: Status


@dataclass
class ScenarioResult:
    name: str
    description: str
    status: Status = Status.PASS
    steps: list[StepResult] = field(default_factory=list)
    error: str | None = None

    def add_step(
        self, action: str, expected: str, actual: str, status: Status
    ) -> StepResult:
        step = StepResult(
            action=action, expected=expected, actual=actual, status=status
        )
        self.steps.append(step)
        if status == Status.FAIL and self.status != Status.FAIL:
            self.status = Status.PARTIAL
        return step

    def mark_fail(self, error: str) -> None:
        self.status = Status.FAIL
        self.error = error

    def finalize(self) -> None:
        if not self.steps:
            return
        fail_count = sum(1 for s in self.steps if s.status == Status.FAIL)
        if fail_count == 0:
            self.status = Status.PASS
        elif fail_count == len(self.steps):
            self.status = Status.FAIL
        else:
            self.status = Status.PARTIAL


class Scenario:
    name: str = "unnamed"
    description: str = ""

    async def run(
        self,
        client: PlatformClient,
        runner: AgentRunner,
        agent: AgentInfo,
        room_id: str,
        shared: dict | None = None,
    ) -> ScenarioResult:
        raise NotImplementedError
