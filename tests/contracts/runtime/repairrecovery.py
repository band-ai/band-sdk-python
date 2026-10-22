from __future__ import annotations

from typing import Any

from remote_agent.program import Artifact
from remote_agent.store import Store
from tests.contracts.runtime import hybrid
from tests.contracts.runtime.program import SOURCE


class InterruptedHost(BaseException):
    pass


class RepairRecoveryFixture(hybrid.HybridFixture):
    def start_repairable(self) -> Any:
        self.provider.candidate = Artifact.publish(
            SOURCE + "\n# validated replacement\n", self.path / "artifacts"
        )
        return hybrid.HybridFixture.start(
            self,
            control_actors={"operator": ["repair", "takeover"]},
            validator=lambda candidate, bundle: {
                "ok": True,
                "checks": ["fixture semantics"],
            },
        )

    def restart(self) -> Any:
        assert self.agent is not None, "The test must start an agent before restarting"
        self.agent.close()
        self.store.close()
        self.store = Store(self.path / "agent.sqlite3")
        self.addCleanup(self.store.close)
        return self.start_repairable()

    def request(self, agent: Any, *, running: bool = False) -> Any:
        agent._request_repair(
            {"reason": "Qualify replacement", "input": None, "needs_handled": False}
        )
        repair = self.store.get("repair")
        if running:
            repair = dict(repair, status="running", reserved=True)
            self.store.commit(
                "repair_started", {}, updates={"repair": repair, "repair_calls": 1}
            )
        return repair
