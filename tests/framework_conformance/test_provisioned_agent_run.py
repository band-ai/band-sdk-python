"""How ``running_provisioned_agent`` hands a bespoke run its execution policy and its turn observer.

The platform connection is the un-runnable boundary here, so ``running_agent`` is
replaced by a stand-in that records what it was started with; the adapter is real,
and so are the observer and its turns.
"""

from __future__ import annotations

from band.runtime.sessionconfig import SessionConfig
from tests.e2e.baseline.toolkit.invocations import InvocationObserver
from tests.e2e.baseline.toolkit.provisioning import running_provisioned_agent
from tests.framework_conformance.invocationkit import (
    ROOM_ID,
    DecliningAdapter,
    ProvisionedResources,
    original,
    turn_input,
)

DEADLINE_S = 5.0


class TestProvisionedAgentRun:
    async def test_the_turns_of_the_adapter_are_observed(
        self, started_with: list[SessionConfig | None]
    ) -> None:
        adapter, observer = (
            DecliningAdapter(),
            InvocationObserver(deadline_s=DEADLINE_S),
        )

        async with running_provisioned_agent(
            adapter, ProvisionedResources(), observer=observer
        ):
            await adapter.on_messages(turn_input(original("m1"), original("m2")))

        assert observer.partition(ROOM_ID) == [("m1", "m2")]
