from __future__ import annotations

from typing import Any

from remote_agent.store import Store
from tests.contracts.runtime import hybrid


class InterruptedHost(BaseException):
    pass


class ModelJournalFixture(hybrid.HybridFixture):
    def restart(self) -> Any:
        assert self.agent is not None, "The test must start an agent before restarting"
        self.agent.close()
        self.store.close()
        self.store = Store(self.path / "agent.sqlite3")
        self.addCleanup(self.store.close)
        return self.start()
