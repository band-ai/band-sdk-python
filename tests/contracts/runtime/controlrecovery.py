from __future__ import annotations

from typing import Any
from unittest.mock import patch

from tests.contracts.runtime import hybrid


class ControlRecoveryFixture(hybrid.HybridFixture):
    def start_controlled(self) -> Any:
        return hybrid.HybridFixture.start(
            self,
            control_actors={"operator": ["takeover", "retry", "resume"]},
            validator=lambda candidate, bundle: {
                "ok": True,
                "checks": ["fixture semantics"],
            },
        )

    def unresolved(self, agent: Any, kind: Any, text: str = "ping") -> None:
        commit = self.store.commit

        def fail_output(name: Any, *args: Any, **kwargs: Any) -> Any:
            if name == kind:
                raise RuntimeError("result commit failed")
            return commit(name, *args, **kwargs)

        with patch.object(self.store, "commit", side_effect=fail_output):
            self.assertEqual(agent.receive(self.question(text))["status"], "held")
