"""Which ask a request id names, and where that ask stands."""

from __future__ import annotations

import pytest

from band.adapters.opencode.approvals.pending import (
    AskKind,
    AskState,
    NamedAsk,
    PendingAsks,
)
from tests.adapters.opencode.approvals.kit import asks_with


class TestNamedAsks:
    @pytest.mark.parametrize(
        ("asks", "request_id", "expected"),
        [
            pytest.param(
                asks_with(permissions=("p-1",)),
                "p-1",
                NamedAsk(AskKind.PERMISSION, AskState.WAITING),
                id="permission-waiting",
            ),
            pytest.param(
                asks_with(permissions=("p-1",), in_flight=("p-1",)),
                "p-1",
                NamedAsk(AskKind.PERMISSION, AskState.IN_FLIGHT),
                id="permission-in-flight",
            ),
            pytest.param(
                asks_with(known_permissions=frozenset({"p-1"})),
                "p-1",
                NamedAsk(AskKind.PERMISSION, AskState.RESOLVED),
                id="permission-resolved",
            ),
            pytest.param(
                asks_with(questions=("q-1",)),
                "q-1",
                NamedAsk(AskKind.QUESTION, AskState.WAITING),
                id="question-waiting",
            ),
            pytest.param(
                asks_with(questions=("q-1",), in_flight=("q-1",)),
                "q-1",
                NamedAsk(AskKind.QUESTION, AskState.IN_FLIGHT),
                id="question-in-flight",
            ),
            pytest.param(
                asks_with(known_questions=frozenset({"q-1"})),
                "q-1",
                NamedAsk(AskKind.QUESTION, AskState.RESOLVED),
                id="question-resolved",
            ),
            pytest.param(
                asks_with(permissions=("p-1",), questions=("q-1",)),
                "never-asked",
                None,
                id="never-asked",
            ),
        ],
    )
    def test_an_id_names_its_ask_and_where_it_stands(
        self, asks: PendingAsks, request_id: str, expected: NamedAsk | None
    ) -> None:
        assert asks.named_ask(request_id) == expected
