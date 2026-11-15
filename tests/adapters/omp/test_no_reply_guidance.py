"""Native OMP advertises whole-turn decline plus yield, not CLI Done completion."""

from __future__ import annotations

import re

from band_sdk_core import no_reply_tool

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.adapters.omp.turns import NATIVE_TURN_COMPLETION_GUIDANCE
from band.integrations.omprpc.decline import (
    NATIVE_BACKLOG_YIELD_GUIDANCE,
    NATIVE_QUEUED_INPUT_GUIDANCE,
    NATIVE_REFUSED_DECLINE_GUIDANCE,
)
from band.runtime.prompts import TURN_COMPLETION_GUIDANCE

CANONICAL_DESCRIPTION = no_reply_tool()["description"]
SINGLE_MESSAGE = re.compile(r"\b(?:latest|current|last) message\b", re.IGNORECASE)


class TestOmpNoReplyGuidance:
    """OMP system context wires native yield cues and never CLI Done guidance."""

    def test_advertises_canonical_decline_and_native_yield_not_cli_done(self) -> None:
        advertised = OmpAdapter(
            OmpAdapterConfig(cwd="."), emit=()
        )._build_system_context()

        assert CANONICAL_DESCRIPTION in advertised
        assert NATIVE_TURN_COMPLETION_GUIDANCE in advertised
        assert NATIVE_BACKLOG_YIELD_GUIDANCE in advertised
        assert NATIVE_REFUSED_DECLINE_GUIDANCE in advertised
        assert NATIVE_QUEUED_INPUT_GUIDANCE in advertised
        assert TURN_COMPLETION_GUIDANCE not in advertised
        assert SINGLE_MESSAGE.findall(NATIVE_TURN_COMPLETION_GUIDANCE) == []
