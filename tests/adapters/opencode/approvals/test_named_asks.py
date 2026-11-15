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
        ("asks", "request_id", "prefer", "expected"),
        [
            pytest.param(
                asks_with(permissions=("p-1",)),
                "p-1",
                AskKind.PERMISSION,
                NamedAsk(AskKind.PERMISSION, AskState.WAITING),
                id="permission-waiting",
            ),
            pytest.param(
                asks_with(permissions=("p-1",), in_flight=("p-1",)),
                "p-1",
                AskKind.PERMISSION,
                NamedAsk(AskKind.PERMISSION, AskState.IN_FLIGHT),
                id="permission-in-flight",
            ),
            pytest.param(
                asks_with(known_permissions=frozenset({"p-1"})),
                "p-1",
                AskKind.PERMISSION,
                NamedAsk(AskKind.PERMISSION, AskState.RESOLVED),
                id="permission-resolved",
            ),
            pytest.param(
                asks_with(questions=("q-1",)),
                "q-1",
                AskKind.PERMISSION,
                NamedAsk(AskKind.QUESTION, AskState.WAITING),
                id="question-waiting",
            ),
            pytest.param(
                asks_with(questions=("q-1",), in_flight=("q-1",)),
                "q-1",
                AskKind.PERMISSION,
                NamedAsk(AskKind.QUESTION, AskState.IN_FLIGHT),
                id="question-in-flight",
            ),
            pytest.param(
                asks_with(known_questions=frozenset({"q-1"})),
                "q-1",
                AskKind.PERMISSION,
                NamedAsk(AskKind.QUESTION, AskState.RESOLVED),
                id="question-resolved",
            ),
            pytest.param(
                asks_with(permissions=("p-1",), questions=("q-1",)),
                "never-asked",
                AskKind.PERMISSION,
                None,
                id="never-asked",
            ),
            pytest.param(
                asks_with(permissions=("shared",), questions=("shared",)),
                "shared",
                AskKind.PERMISSION,
                NamedAsk(AskKind.PERMISSION, AskState.WAITING),
                id="held-as-both-prefers-permission",
            ),
            pytest.param(
                asks_with(permissions=("shared",), questions=("shared",)),
                "shared",
                AskKind.QUESTION,
                NamedAsk(AskKind.QUESTION, AskState.WAITING),
                id="held-as-both-prefers-question",
            ),
            pytest.param(
                asks_with(
                    known_permissions=frozenset({"shared"}),
                    known_questions=frozenset({"shared"}),
                ),
                "shared",
                AskKind.QUESTION,
                NamedAsk(AskKind.QUESTION, AskState.RESOLVED),
                id="known-as-both-prefers-question",
            ),
            pytest.param(
                asks_with(permissions=("p-1",), known_questions=frozenset({"p-1"})),
                "p-1",
                AskKind.QUESTION,
                NamedAsk(AskKind.PERMISSION, AskState.WAITING),
                id="held-beats-a-preferred-resolved-kind",
            ),
        ],
    )
    def test_an_id_names_its_ask_and_where_it_stands(
        self,
        asks: PendingAsks,
        request_id: str,
        prefer: AskKind,
        expected: NamedAsk | None,
    ) -> None:
        assert asks.named_ask(request_id, prefer=prefer) == expected
