"""Rule specification regression checks."""

from __future__ import annotations

import unittest

from tests.contracts.spec.oldformguard.kit import HEAD, OLD, verdicts


class TestRule(unittest.TestCase):
    def test_an_untimed_request_answered_in_its_target_is_an_error_at_the_request(
        self,
    ) -> None:
        self.assertEqual(verdicts(OLD), [(2, "error")])

    def test_a_timed_answer_makes_even_a_timed_request_an_error(self) -> None:
        source = OLD.replace("request() =>", "request() @1m =>").replace(
            "review() =>", "review() @5m =>"
        )
        self.assertEqual(
            verdicts(source.replace("=> done", "=> done | done")), [(2, "error")]
        )

    def test_a_timed_request_whose_answers_are_untimed_is_an_owed_report(self) -> None:
        self.assertEqual(
            verdicts(OLD.replace("request() =>", "request() @1m =>")), [(2, "exempt")]
        )

    def test_reply_heads_and_re_sends_into_their_wait_are_clean(self) -> None:
        self.assertEqual(verdicts(HEAD), [])

    def test_one_way_sends_and_anystate_answers_are_not_requests(self) -> None:
        self.assertEqual(
            verdicts(
                OLD.replace(
                    "reviewer -> lead: review() => done",
                    "lead -> reviewer: nudge() => done",
                )
            ),
            [],
        )
        anystate = OLD.replace(
            "state reviewing\n  reviewer", "state reviewing\nanystate\n  reviewer"
        )
        self.assertEqual(verdicts(anystate), [])

    def test_events_and_self_moves_are_not_answers(self) -> None:
        self.assertEqual(
            verdicts(
                OLD.replace(
                    "  reviewer -> lead: review() => done",
                    "  event reviewer -> lead: review() => done\n"
                    "  reviewer -> lead: note() => @self",
                )
            ),
            [],
        )

    def test_sets_rosters_and_each_are_group_requests_for_section_3(self) -> None:
        self.assertEqual(
            verdicts(OLD.replace("lead -> reviewer", "lead -> {reviewer,other}")),
            [(2, "group")],
        )
        roster = "reviewer: list(member)\n" + OLD
        self.assertEqual(
            verdicts(
                roster.replace("lead -> reviewer", "lead -> reviewer").replace(
                    "request() =>", "request() each =>"
                )
            ),
            [(3, "group")],
        )
        self.assertEqual(
            verdicts(
                "reviewers: list(reviewer)\n" + OLD.replace("reviewer", "reviewers")
            ),
            [(3, "group")],
        )

    def test_a_binding_named_each_is_not_the_each_modifier(self) -> None:
        self.assertEqual(
            verdicts(OLD.replace("request() =>", "request() as each =>")),
            [(2, "error")],
        )

    def test_doc_blocks_notes_and_sigil_prompts_do_not_end_a_section(self) -> None:
        source = OLD.replace(
            "state reviewing\n",
            'state reviewing\n  ~s"""\n  state x\n  """\n  /* a\n  note */\n',
        )
        self.assertEqual(verdicts(source), [(2, "error")])
