"""An approval turn closes only on a reply after the last request and notice."""

from __future__ import annotations

from band.adapters.opencode.approvals.wording import APPROVAL_REQUESTED_TEMPLATE
from tests.e2e.baseline.smoke.samples.approvals import DIALECTS, Notice
from tests.e2e.baseline.toolkit.adapters import Adapter
from tests.framework_conformance.approvalcommands.kit import agent_message


class TestApprovalClosure:
    def test_approval_closure_requires_reply_after_the_last_request_and_notice(
        self,
    ) -> None:
        dialect = DIALECTS[Adapter.OPENCODE]
        closing_reply = "turn-finished"
        request = APPROVAL_REQUESTED_TEMPLATE.format(
            permission="bash", patterns="cat approval.txt", request_id="follow-up"
        )
        notice = Notice("OpenCode approval `follow-up` handled with `reject`.")

        before_close = [
            agent_message("Checking the result."),
            agent_message(request),
            agent_message(notice.text),
        ]
        assert not dialect.settled(before_close, notice, closing_reply=closing_reply)
        assert not dialect.settled(
            [*before_close, agent_message("Checking the next command.")],
            notice,
            closing_reply=closing_reply,
        )
        assert not dialect.settled(
            [
                *before_close,
                agent_message(f"I will finish with {closing_reply} after checking."),
            ],
            notice,
            closing_reply=closing_reply,
        )
        for closing in (
            f"@[[agent]] {closing_reply}",
            f"`{closing_reply}`",
            f"The command was declined. {closing_reply}",
        ):
            assert dialect.settled(
                [*before_close, agent_message(closing)],
                notice,
                closing_reply=closing_reply,
            ), closing
