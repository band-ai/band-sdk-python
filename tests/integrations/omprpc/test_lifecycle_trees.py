"""Interrupted RPC lifecycle sequences retain custody of real descendant trees."""

from __future__ import annotations

import pytest

from tests.integrations.omprpc.kit import ProcessGate
from tests.integrations.omprpc.lifecycle import Step, play


class TestRpcLifecycleTrees:
    @pytest.mark.parametrize("detached", [False, True], ids=["session", "detached"])
    @pytest.mark.parametrize(
        "steps",
        [
            pytest.param(
                [Step.PROMPT, Step.CANCEL_QUEUED, Step.SETTLE],
                id="cancel-queued-then-settle",
            ),
            pytest.param(
                [Step.PROMPT, Step.CRASH, Step.PROMPT, Step.CANCEL_OWNER],
                id="crash-then-cancel-replacement",
            ),
            pytest.param(
                [Step.CANCEL_START_RESTART, Step.PROMPT, Step.CLOSE],
                id="cancel-start-then-close-replacement",
            ),
            pytest.param(
                [Step.CANCEL_CLOSE_RESTART, Step.PROMPT, Step.SETTLE],
                id="cancel-close-then-settle-replacement",
            ),
            pytest.param(
                [
                    Step.CANCEL_CLOSE_RESTART,
                    Step.CANCEL_OWNER,
                    Step.CANCEL_CLOSE_RESTART,
                ],
                id="cancel-close-around-owner-cancellation",
            ),
            pytest.param(
                [Step.PROMPT, Step.PROMPT, Step.CANCEL_OWNER, Step.CANCEL_OWNER],
                id="repeat-prompt-and-owner-cancellation",
            ),
            pytest.param(
                [
                    Step.CANCEL_CLOSE_RESTART,
                    Step.CANCEL_OWNER,
                    Step.CANCEL_CLOSE_RESTART,
                    Step.CANCEL_CLOSE_RESTART,
                    Step.CANCEL_OWNER,
                    Step.CANCEL_OWNER,
                ],
                id="repeated-interrupted-restarts",
            ),
        ],
    )
    async def test_interrupted_operations_reap_owned_trees_and_recover(
        self, process_gate: ProcessGate, steps: list[Step], detached: bool
    ) -> None:
        await play(steps, gate=process_gate, detached=detached)
