from __future__ import annotations

import pytest

from tests.contracts.runtime.bandenginebudgets import BundledWaitBudgetFixture


@pytest.mark.contract_engine("band")
class TestBundledWaitBudget(BundledWaitBudgetFixture):
    def test_head_entry_ends_before_promising_a_window_longer_than_the_remaining_total(
        self,
    ) -> None:
        self.assert_insufficient("head")

    def test_resend_entry_ends_before_promising_a_window_longer_than_the_remaining_total(
        self,
    ) -> None:
        self.assert_insufficient("resend")

    def test_head_entry_keeps_a_reply_window_that_exactly_fits(self) -> None:
        self.assert_exact_fit("head")

    def test_resend_entry_keeps_a_reply_window_that_exactly_fits(self) -> None:
        self.assert_exact_fit("resend")

    def test_reply_extensions_still_require_explicit_host_capabilities(self) -> None:
        self.assertTrue(
            all(row["default_extension_refused"] for row in self.rows.values())
        )
