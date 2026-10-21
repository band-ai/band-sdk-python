from __future__ import annotations

import pytest

from band.adapters.opencode.approvals.commands import PermissionCommand
from band.adapters.opencode.approvals.pending import PendingAsks
from band.adapters.opencode.approvals.router import (
    NamedQuestionGone,
    PermissionReply,
    QuestionAnswer,
    QuestionRejection,
    ReplyRoute,
    route_reply,
)
from tests.adapters.opencode.approvals.kit import asks_with


def outline(route: ReplyRoute | None) -> tuple[str, str | None] | None:
    """The route's kind and the request or text it carries."""
    match route:
        case None:
            return None
        case PermissionReply(PermissionCommand(reply, request_id)):
            return (f"permission {reply}", request_id)
        case QuestionRejection(question):
            return ("question reject", question.request_id)
        case QuestionAnswer(question, text):
            return (f"question answer {question.request_id}", text)
        case NamedQuestionGone(request_id):
            return ("question gone", request_id)


class TestReplyRouting:
    @pytest.mark.parametrize(
        ("asks", "content", "expected"),
        [
            (asks_with(), "approve", None),
            (asks_with(), "   ", None),
            (asks_with(permissions=("p-1",)), "approve", ("permission once", None)),
            (
                asks_with(permissions=("p-1",)),
                "@agent reject p-1",
                ("permission reject", "p-1"),
            ),
            (
                asks_with(questions=("q-1",)),
                "@agent blue",
                ("question answer q-1", "blue"),
            ),
            (
                asks_with(questions=("q-1",)),
                "@agent @bob is the answer",
                ("question answer q-1", "@bob is the answer"),
            ),
            (
                asks_with(questions=("q-1",)),
                "reject q-1",
                ("question reject", "q-1"),
            ),
            (
                asks_with(permissions=("p-1",), questions=("q-1",)),
                "reject q-1",
                ("question reject", "q-1"),
            ),
            (
                asks_with(questions=("q-1",)),
                "reject q-gone",
                ("question gone", "q-gone"),
            ),
            (
                asks_with(known_questions=frozenset({"q-old"})),
                "reject q-old",
                ("question gone", "q-old"),
            ),
            (
                asks_with(known_permissions=frozenset({"p-old"})),
                "approve p-old",
                ("permission once", "p-old"),
            ),
        ],
    )
    def test_message_goes_to_the_ask_it_answers(
        self, asks: PendingAsks, content: str, expected: tuple[str, str | None] | None
    ) -> None:
        assert outline(route_reply(asks, content)) == expected

    def test_routing_is_free_of_side_effects(self) -> None:
        asks = asks_with(permissions=("p-1",), questions=("q-1",))

        route_reply(asks, "approve p-1")
        route_reply(asks, "answer")

        assert not asks.has_claimed()
        assert len(asks.permissions) == len(asks.questions) == 1
