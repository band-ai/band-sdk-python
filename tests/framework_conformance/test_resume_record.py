"""No adapter posts a resume record before its session's seed is confirmed."""

from __future__ import annotations

import pytest

from tests.framework_conformance.outcomeprobes import band_adapters
from tests.framework_conformance.resumeprobes import SeedOutcome
from tests.framework_conformance.resumerecords import (
    OUTSIDE_THE_RULE,
    RESUME_PROBES,
    probed,
    resume_records,
    uses_session_resume,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_tools


class TestResumeRecord:
    @pytest.mark.parametrize("adapter", sorted(RESUME_PROBES))
    async def test_a_failed_seed_posts_no_resume_record(self, adapter: str) -> None:
        probe, tools = RESUME_PROBES[adapter], turn_tools()

        with pytest.raises(probe.seed_failure()):
            await probe.seed(SeedOutcome.FAILED, tools)

        assert resume_records(probe.record, tools) == []

    @pytest.mark.parametrize("adapter", sorted(RESUME_PROBES))
    async def test_a_confirmed_seed_records_the_session_with_narration_silenced(
        self, adapter: str
    ) -> None:
        probe, tools = RESUME_PROBES[adapter], turn_tools()

        await probe.seed(SeedOutcome.CONFIRMED, tools)

        assert list(probe.record.candidates(tools.events_sent)) == [ROOM_ID]

    def test_every_adapter_using_the_rule_has_a_probe(self) -> None:
        adapters = band_adapters()

        unprobed = {
            name
            for name, cls in adapters.items()
            if uses_session_resume(cls) and not probed(cls, adapters)
        }

        assert unprobed - OUTSIDE_THE_RULE.keys() == set()
